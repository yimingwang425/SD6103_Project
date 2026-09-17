#!/usr/bin/env python3
"""合并 HDB 转售 CSV，保留后续关联所需属性，填充租期并拆分楼层。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation
from pathlib import Path


INT_MAX = 2_147_483_647
INITIAL_LEASE_YEARS = 99
PRICE_MAX = Decimal("9999999999.99")  # NUMERIC(12,2)：10 位整数、2 位小数
CENT = Decimal("0.01")
OUTPUT_COLUMNS = [
    "TransactionID", "TransactionYear", "TransactionMonth", "RemainingLease",
    "ResalePrice", "town", "flat_type", "block", "street_name",
    "storey_range_min", "storey_range_max", "floor_area_sqm", "flat_model",
]
TEXT_ATTRIBUTES = ["town", "flat_type", "block", "street_name", "flat_model"]
REQUIRED_COLUMNS = set(TEXT_ATTRIBUTES + [
    "month", "resale_price", "storey_range", "floor_area_sqm", "lease_commence_date",
])
INTEGER_COLUMNS = [
    "TransactionID", "TransactionYear", "TransactionMonth", "RemainingLease",
    "storey_range_min", "storey_range_max",
]
MISSING = {"", "NA", "N/A", "NAN", "NULL", "NONE", "-"}
AUDIT_COLUMNS = [
    "TransactionID", "SourceFile", "SourceRecord", "DateBasis",
    "RemainingLeaseSource", "RemainingLeasePrecision",
    "RemainingLeaseUnit", "LeaseCommenceYear", "OriginalRemainingLease",
    "OriginalStoreyRange",
    "PotentialDuplicate", "FirstMatchingTransactionID",
]
REJECT_COLUMNS = ["SourceFile", "SourceRecord", "ErrorCode", "Reason", "RawRecord"]


class DataError(ValueError):
    """某条数据无法安全转换；与文件/程序错误区分。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def missing(value: str | None) -> bool:
    return clean_text(value) in MISSING


def decimal_value(value: str | None, field: str) -> Decimal:
    if missing(value):
        raise DataError("missing_value", f"{field} 缺失")
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        raise DataError("invalid_number", f"{field} 不是有效数值：{value!r}") from None
    if not number.is_finite():
        raise DataError("invalid_number", f"{field} 必须是有限数值")
    return number


def positive_int(value: str | None, field: str) -> int:
    number = decimal_value(value, field)
    if number != number.to_integral_value() or not 1 <= number <= INT_MAX:
        raise DataError("invalid_integer", f"{field} 必须是 1—{INT_MAX} 的整数")
    return int(number)


def parse_month(value: str | None) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]{4})-([0-9]{2})", (value or "").strip())
    if not match:
        raise DataError("invalid_month", f"month 必须是 YYYY-MM：{value!r}")
    year, month = map(int, match.groups())
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise DataError("invalid_month", f"无效年月：{value!r}")
    return year, month


def parse_price(value: str | None) -> str:
    """全程 Decimal；绝不通过 float 或静默四舍五入改变成交价。"""
    text = (value or "").strip()
    # 支持正规千位分隔符；拒绝 12,34 这样的错误格式。
    if "," in text:
        if not re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", text):
            raise DataError("invalid_price", f"价格的千位分隔符不合法：{value!r}")
        text = text.replace(",", "")
    number = decimal_value(text, "resale_price")
    if not 0 < number <= PRICE_MAX:
        raise DataError("invalid_price", "价格必须大于 0，且不超过 NUMERIC(12,2) 上限")
    rounded = number.quantize(CENT)
    if number != rounded:
        raise DataError("price_precision", "价格含有超过 2 位的有效小数，未自动舍入")
    return format(rounded, ".2f")


def parse_remaining_lease(
    value: str | None, transaction_year: int, lease_year: int | None,
) -> tuple[int, str, str, int]:
    """返回完整年、来源、原始精度、余下月份；月份仅用于区分疑似重复记录。"""
    if missing(value):
        if lease_year is None:
            raise DataError("missing_lease_year", "剩余租期与租约起始年均缺失，无法按公式填充")
        if lease_year > transaction_year:
            raise DataError("invalid_lease_estimate", "租约起始年晚于交易年，无法按年估算")
        # month 是 YYYY-MM；起租日期只有年份，所以提取交易年份后相减。
        # 不假设起租月份为 1 月，避免制造原始数据没有的月份精度。
        years = INITIAL_LEASE_YEARS - (transaction_year - lease_year)
        remainder_months = 0
        origin, precision = "estimated_from_years", "year_estimate"
    else:
        text = clean_text(value)
        # 原始整数年可能被保存为 70 或 70.0。
        if re.fullmatch(r"[0-9]+(?:\.0+)?", text):
            years = int(Decimal(text))
            remainder_months = 0
            origin, precision = "reported", "year"
        else:
            match = re.fullmatch(
                r"(?:(\d+)\s*YEARS?)?\s*(?:(\d+)\s*MONTHS?)?", text,
            )
            if not match or not any(item is not None for item in match.groups()):
                raise DataError("invalid_lease", f"无法解析剩余租期：{value!r}")
            years_text, months_text = match.groups()
            years_part, months_part = int(years_text or 0), int(months_text or 0)
            if years_text is not None and months_part > 11:
                raise DataError("invalid_lease", "年＋月格式中的月份分量必须为 0—11")
            # 主结果要求整数年，舍去不足一年的部分；原始月份仍用于审计和重复检测。
            extra_years, remainder_months = divmod(months_part, 12)
            years = years_part + extra_years
            origin = "reported"
            precision = "month" if months_text is not None else "year"
    if not 0 <= years <= INITIAL_LEASE_YEARS or (years == INITIAL_LEASE_YEARS and remainder_months):
        raise DataError("invalid_lease", "剩余租期超出 0—99 年范围")
    return years, origin, precision, remainder_months


def parse_storey_range(value: str | None) -> tuple[int, int]:
    """例如 01 TO 03 -> (1, 3)，不计算平均楼层。"""
    match = re.fullmatch(r"([0-9]+)\s*(?:TO|-)\s*([0-9]+)", clean_text(value))
    if not match:
        raise DataError("invalid_storey_range", f"无效楼层区间：{value!r}")
    minimum, maximum = map(int, match.groups())
    if not 1 <= minimum <= maximum <= INT_MAX:
        raise DataError("invalid_storey_range", "楼层必须满足 1 <= min <= max <= INT 上限")
    return minimum, maximum


def clean_attributes(row: dict[str, str]) -> dict:
    """规范供后续关联使用的属性；此处不生成或匹配 FlatID。"""
    values = {}
    for field in TEXT_ATTRIBUTES:
        if missing(row.get(field)):
            raise DataError("missing_flat_attribute", f"后续关联所需属性缺失：{field}")
        values[field] = clean_text(row[field])
    if values["flat_type"] == "MULTI GENERATION":
        values["flat_type"] = "MULTI-GENERATION"
    values["storey_range_min"], values["storey_range_max"] = parse_storey_range(row.get("storey_range"))
    area = decimal_value(row["floor_area_sqm"], "floor_area_sqm")
    if not 0 < area < Decimal("1000000"):
        raise DataError("invalid_area", "面积必须大于 0 且小于 1000000 平方米")
    # 31、31.0、31.00 应匹配同一个键；不用 normalize()，避免上下文舍入。
    area_text = format(area, "f")
    values["floor_area_sqm"] = area_text.rstrip("0").rstrip(".") if "." in area_text else area_text
    return values


def csv_reader(handle, required: set[str]) -> csv.DictReader:
    reader = csv.DictReader(handle, strict=True)
    if not reader.fieldnames:
        raise ValueError(f"空文件或缺少表头：{handle.name}")
    fields = [field.strip().lstrip("\ufeff").lower() for field in reader.fieldnames]
    if len(fields) != len(set(fields)) or any(not name for name in fields):
        raise ValueError(f"表头包含重名列或空列名：{handle.name}")
    absent = required - set(fields)
    if absent:
        raise ValueError(f"{handle.name} 缺少必需列：{', '.join(sorted(absent))}")
    reader.fieldnames = fields
    return reader


def check_row_width(row: dict) -> None:
    if None in row or any(value is None for value in row.values()):
        raise DataError("column_count", "该记录的字段数量与表头不一致")


def date_basis(path: Path) -> str:
    name = path.name.lower()
    if "approval date" in name:
        return "approval"
    if "registration date" in name:
        return "registration"
    return "unknown"


def under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def discover_files(source: Path, destination: Path) -> list[Path]:
    if not source.is_dir():
        raise ValueError(f"输入目录不存在：{source}")
    if under(source, destination):
        raise ValueError("输出目录不能等于或包含输入目录")
    files = [
        path.resolve() for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() == ".csv"
        and not under(path.resolve(), destination)
    ]
    files.sort(key=lambda path: (path.relative_to(source).as_posix().casefold(), path.as_posix()))
    if not files:
        raise ValueError("输入目录中没有可处理的 CSV 文件")
    return files


def make_writer(handle, columns: list[str]) -> csv.DictWriter:
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    return writer


def clean_files(args: argparse.Namespace) -> dict:
    """显式运行时才读取全部 CSV、生成结果；不修改输入文件。"""
    source = Path(args.input).expanduser().resolve()
    destination = Path(args.output).expanduser().resolve()
    if destination.exists():
        raise ValueError("输出目录已经存在，请指定新的目录；脚本不会覆盖已有结果")
    files = discover_files(source, destination)
    # 先检查全部表头，防止漏处理不兼容文件。CSV 语法/编码损坏会使整次运行失败。
    for path in files:
        with path.open("r", encoding=args.encoding, newline="") as handle:
            csv_reader(handle, REQUIRED_COLUMNS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".resale-staging-", dir=destination.parent))
    report = {
        "schema": {
            name: "INT" if name in INTEGER_COLUMNS else
            "NUMERIC(12,2)" if name == "ResalePrice" else
            "NUMERIC" if name == "floor_area_sqm" else "TEXT"
            for name in OUTPUT_COLUMNS
        },
        "remaining_lease_unit": "years",
        "missing_lease_policy": "estimate_from_years",
        "remaining_lease_formula_years": "99 - (TransactionYear - lease_commence_date)",
        "remaining_lease_rounding": "floor_full_years",
        "initial_lease_years_assumption": INITIAL_LEASE_YEARS,
        "transaction_id_start": args.transaction_id_start,
        "duplicate_policy": "keep_and_flag",
        "lease_sources": Counter(), "rejection_reasons": Counter(), "files": [],
        "input_rows": 0, "accepted_rows": 0, "rejected_rows": 0,
        "potential_duplicate_rows": 0,
    }
    # 一个指纹只用来标记疑似重复，不作为交易主键，也不用于删除记录。
    seen_records: dict[bytes, int] = {}
    next_transaction_id = args.transaction_id_start
    try:
        # ExitStack 兼容 Python 3.8，并在后续文件打开失败时关闭先前的文件。
        with ExitStack() as stack:
            main_handle = stack.enter_context(
                (staging / "resale_transactions.csv").open("w", encoding="utf-8-sig", newline="")
            )
            audit_handle = stack.enter_context(
                (staging / "audit.csv").open("w", encoding="utf-8-sig", newline="")
            )
            rejected_handle = stack.enter_context(
                (staging / "rejected_rows.csv").open("w", encoding="utf-8-sig", newline="")
            )
            main_writer = make_writer(main_handle, OUTPUT_COLUMNS)
            audit_writer = make_writer(audit_handle, AUDIT_COLUMNS)
            rejected_writer = make_writer(rejected_handle, REJECT_COLUMNS)
            for path in files:
                relative = path.relative_to(source).as_posix()
                basis = date_basis(path)
                file_report = {"file": relative, "date_basis": basis, "input_rows": 0, "accepted_rows": 0, "rejected_rows": 0}
                report["files"].append(file_report)
                with path.open("r", encoding=args.encoding, newline="") as handle:
                    reader = csv_reader(handle, REQUIRED_COLUMNS)
                    file_report["remaining_lease_column_present"] = "remaining_lease" in reader.fieldnames
                    for ordinal, row in enumerate(reader, 1):
                        if next_transaction_id > INT_MAX:
                            raise ValueError("TransactionID 超出 INT 范围，请更换编号方案")
                        transaction_id = next_transaction_id
                        next_transaction_id += 1  # 拒绝的记录也占一个序号，便于追溯。
                        report["input_rows"] += 1
                        file_report["input_rows"] += 1
                        try:
                            check_row_width(row)
                            year, month = parse_month(row["month"])
                            attributes = clean_attributes(row)
                            price = parse_price(row["resale_price"])
                            lease_year = None if missing(row["lease_commence_date"]) else positive_int(
                                row["lease_commence_date"], "lease_commence_date",
                            )
                            if lease_year is not None and lease_year > 9999:
                                raise DataError("invalid_lease_year", "租约起始年份超出 1—9999 范围")
                            lease_years, lease_source, lease_precision, lease_remainder_months = parse_remaining_lease(
                                row.get("remaining_lease"), year, lease_year,
                            )
                        except DataError as error:
                            rejected_writer.writerow({
                                "SourceFile": relative, "SourceRecord": ordinal,
                                "ErrorCode": error.code, "Reason": str(error),
                                "RawRecord": json.dumps(row, ensure_ascii=False),
                            })
                            report["rejected_rows"] += 1
                            file_report["rejected_rows"] += 1
                            report["rejection_reasons"][error.code] += 1
                            continue

                        # 虽然主结果输出完整年，仍用原始余月区分61年4月和61年8月。
                        canonical = json.dumps(
                            [attributes, lease_year, year, month, [lease_years, lease_remainder_months], price],
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        )
                        fingerprint = hashlib.sha256(canonical.encode("utf-8")).digest()
                        first_id = seen_records.get(fingerprint)
                        duplicate = first_id is not None
                        if not duplicate:
                            seen_records[fingerprint] = transaction_id
                        report["potential_duplicate_rows"] += int(duplicate)
                        main_writer.writerow({
                            "TransactionID": transaction_id,
                            "TransactionYear": year, "TransactionMonth": month,
                            "RemainingLease": lease_years, "ResalePrice": price,
                            **attributes,
                        })
                        audit_writer.writerow({
                            "TransactionID": transaction_id, "SourceFile": relative,
                            "SourceRecord": ordinal, "DateBasis": basis,
                            "RemainingLeaseSource": lease_source,
                            "RemainingLeasePrecision": lease_precision,
                            "RemainingLeaseUnit": "years",
                            "LeaseCommenceYear": lease_year,
                            "OriginalRemainingLease": row.get("remaining_lease", ""),
                            "OriginalStoreyRange": row["storey_range"],
                            "PotentialDuplicate": int(duplicate),
                            "FirstMatchingTransactionID": first_id if duplicate else "",
                        })
                        report["accepted_rows"] += 1
                        file_report["accepted_rows"] += 1
                        report["lease_sources"][lease_source] += 1

        if report["input_rows"] != report["accepted_rows"] + report["rejected_rows"]:
            raise RuntimeError("输入输出行数不平衡")
        report["next_transaction_id"] = next_transaction_id
        report["status"] = "completed_with_rejections" if report["rejected_rows"] else "completed"
        (staging / "quality_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        # 只有全部处理成功才交付结果目录；不会留下看似完整的半成品。
        if destination.exists():
            raise ValueError("输出目录在运行期间已被创建，请使用其他输出目录")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)  # 仅清理本函数刚创建的专用临时目录。
    return report


def cli_positive_int(value: str) -> int:
    try:
        return positive_int(value, "命令行参数")
    except DataError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="原始 CSV 目录，递归读取")
    parser.add_argument("--output", required=True, help="新的输出目录，不覆盖已存在目录")
    parser.add_argument("--lease-unit", choices=["years"], default="years", help="剩余租期固定为整数年；默认 years，可省略此参数")
    parser.add_argument("--transaction-id-start", type=cli_positive_int, default=1)
    parser.add_argument("--encoding", default="utf-8-sig", help="所有输入 CSV 的编码，默认 utf-8-sig")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 重定向/捕获终端输出时也使用 UTF-8，避免中文帮助和错误信息乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        report = clean_files(args)
    except (OSError, ValueError, csv.Error, InvalidOperation) as error:
        print(f"处理失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps({name: report[name] for name in ["status", "input_rows", "accepted_rows", "rejected_rows", "potential_duplicate_rows"]}, ensure_ascii=False, indent=2))
    print(f"结果目录：{Path(args.output).resolve()}")
    # 2 表示结果已生成，但存在被隔离的记录，提醒调用者检查报告。
    return 2 if report["rejected_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
