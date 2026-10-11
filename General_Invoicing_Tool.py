#!/usr/bin/env python3
"""General-purpose estimates and invoices for repair and handyman businesses.

Run with:
    python -m pip install streamlit fpdf2
    streamlit run handyman_estimate_invoice_app.py

Documents are stored locally in ~/HandymanBusiness by default. Set
HANDYMAN_DOCS_DIR to use a different storage directory.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import streamlit as st
from fpdf import FPDF
from fpdf.enums import MethodReturnValue


# ---------------------------------------------------------------------------
# Paths and defaults
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.environ.get("HANDYMAN_DOCS_DIR", str(Path.home() / "HandymanBusiness"))).expanduser()
ESTIMATE_DIR = DATA_ROOT / "Estimates"
INVOICE_DIR = DATA_ROOT / "Invoices"
ASSET_DIR = DATA_ROOT / "assets"
PROFILE_PATH = DATA_ROOT / "business_profile.json"

CATEGORIES = ["Labor", "Materials", "Parts", "Equipment", "Travel", "Other"]
DEFAULT_ACCENT = "#1F4E79"

DEFAULT_PROFILE: Dict[str, Any] = {
    "business_name": "Your Business Name",
    "contact_name": "",
    "phone": "",
    "email": "",
    "website": "",
    "address": "",
    "city": "",
    "state": "",
    "zip": "",
    "accent_color": DEFAULT_ACCENT,
    "logo_path": "",
}


def ensure_directories() -> None:
    for folder in (DATA_ROOT, ESTIMATE_DIR, INVOICE_DIR, ASSET_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def get_folder(document_type: str) -> Path:
    return INVOICE_DIR if document_type == "Invoice" else ESTIMATE_DIR


def load_profile() -> Dict[str, Any]:
    ensure_directories()
    if PROFILE_PATH.exists():
        try:
            with PROFILE_PATH.open("r", encoding="utf-8") as file:
                saved = json.load(file)
            profile = copy.deepcopy(DEFAULT_PROFILE)
            if isinstance(saved, dict):
                profile.update(saved)
            return profile
        except (OSError, json.JSONDecodeError):
            pass
    return copy.deepcopy(DEFAULT_PROFILE)


def save_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=2, ensure_ascii=False)
    temp_path.replace(path)


# ---------------------------------------------------------------------------
# Calculation and input helpers
# ---------------------------------------------------------------------------


def is_missing(value: Any) -> bool:
    """Safely detect empty values from editable tables and JSON records."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {"<na>", "nan", "none", "nat"}
    if type(value).__name__ == "NAType":
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        pass
    try:
        different_from_self = value != value
        return bool(different_from_self)
    except (TypeError, ValueError):
        return False


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, float) and math.isnan(value):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "").replace("$", "")
        if not text:
            return default
        if text.endswith("%"):
            text = text[:-1].strip()
        return float(text)
    except (TypeError, ValueError):
        return default


def money(value: Any) -> str:
    return f"${as_float(value):,.2f}"


def format_quantity(value: Any) -> str:
    number = as_float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def parse_discount(gross_amount: float, discount_input: Any) -> float:
    """Discount may be entered as e.g. 10%, 25, or $25; never below zero."""
    text = str(discount_input or "").strip().replace(",", "")
    if not text:
        return 0.0
    if text.endswith("%"):
        amount = gross_amount * as_float(text[:-1]) / 100.0
    else:
        amount = as_float(text)
    return round(min(max(amount, 0.0), max(gross_amount, 0.0)), 2)


def cleaned_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        raw_description = raw.get("description", raw.get("desc", ""))
        if is_missing(raw_description):
            continue
        description = str(raw_description).strip()
        if not description:
            continue
        raw_category = raw.get("category", "Other")
        category = "Other" if is_missing(raw_category) else str(raw_category).strip()
        if category not in CATEGORIES:
            category = "Other"
        quantity = max(0.0, as_float(raw.get("quantity", raw.get("qty", 1.0)), 1.0))
        unit_price = max(0.0, as_float(raw.get("unit_price", raw.get("rate", 0.0))))
        taxable_value = raw.get("taxable", False)
        if is_missing(taxable_value):
            taxable_value = False
        elif isinstance(taxable_value, str):
            taxable_value = taxable_value.strip().lower() in {"true", "yes", "1"}
        cleaned.append({
            "category": category,
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "taxable": bool(taxable_value),
        })
    return cleaned


def calculate_totals(data: Dict[str, Any]) -> Dict[str, float]:
    items = cleaned_items(data.get("items", []))
    subtotal = 0.0
    taxable_subtotal = 0.0
    for item in items:
        line_total = round(item["quantity"] * item["unit_price"], 2)
        subtotal += line_total
        if item["taxable"]:
            taxable_subtotal += line_total
    subtotal = round(subtotal, 2)
    taxable_subtotal = round(taxable_subtotal, 2)
    tax_rate = max(0.0, as_float(data.get("tax_rate", 0.0)))
    tax = round(taxable_subtotal * tax_rate / 100.0, 2)
    discount = parse_discount(subtotal + tax, data.get("discount", ""))
    total = round(max(0.0, subtotal + tax - discount), 2)
    amount_paid = max(0.0, as_float(data.get("amount_paid", 0.0)))
    balance = round(total - amount_paid, 2)
    return {
        "subtotal": subtotal,
        "taxable_subtotal": taxable_subtotal,
        "tax_rate": tax_rate,
        "tax": tax,
        "discount": discount,
        "total": total,
        "amount_paid": amount_paid,
        "balance": balance,
        "balance_due": max(balance, 0.0),
        "credit": max(-balance, 0.0),
    }


def safe_document_number(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip(".-_")[:70]
    return cleaned


def next_document_number(document_type: str) -> str:
    folder = get_folder(document_type)
    folder.mkdir(parents=True, exist_ok=True)
    prefix = "INV" if document_type == "Invoice" else "EST"
    today_prefix = f"{prefix}-{date.today():%Y%m%d}-"
    highest = 0
    for candidate in folder.glob(f"{today_prefix}*.json"):
        match = re.match(re.escape(today_prefix) + r"(\d+)$", candidate.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{today_prefix}{highest + 1:03d}"


def new_document(document_type: str) -> Dict[str, Any]:
    today = date.today()
    is_invoice = document_type == "Invoice"
    return {
        "document_type": document_type,
        "doc_number": next_document_number(document_type),
        "doc_date": today.isoformat(),
        "deadline_date": (today + timedelta(days=14 if is_invoice else 30)).isoformat(),
        "reference": "",
        "terms": (
            "Payment is due by the date shown unless otherwise agreed in writing."
            if is_invoice else
            "This estimate is valid until the date shown. Work outside the listed scope may require a revised estimate."
        ),
        "customer_name": "",
        "customer_phone": "",
        "customer_email": "",
        "billing_address": "",
        "billing_city": "",
        "billing_state": "",
        "billing_zip": "",
        "job_title": "",
        "service_address": "",
        "service_city": "",
        "service_state": "",
        "service_zip": "",
        "site_contact": "",
        "items": [{"category": "Labor", "description": "", "quantity": 1.0, "unit_price": 0.0, "taxable": False}],
        "tax_rate": 0.0,
        "discount": "",
        "amount_paid": 0.0,
        "notes": "",
    }


def date_value(value: Any, fallback: date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def display_date(value: Any) -> str:
    parsed = date_value(value, date.today())
    return parsed.strftime("%b %d, %Y")


def pdf_text(value: Any) -> str:
    """Keep built-in PDF fonts safe for common names and punctuation."""
    text = str(value or "")
    replacements = {
        "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2026": "...",
        "\u00a0": " ", "\u2122": "(TM)", "\u00ae": "(R)", "\u00a9": "(C)",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def hex_rgb(value: str) -> Tuple[int, int, int]:
    text = str(value or DEFAULT_ACCENT).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6 or not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        text = DEFAULT_ACCENT.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def profile_contact_lines(profile: Dict[str, Any]) -> List[str]:
    address_bits = [profile.get("address", ""), profile.get("city", ""), profile.get("state", ""), profile.get("zip", "")]
    address = ", ".join(str(part).strip() for part in address_bits if str(part or "").strip())
    contact = " | ".join(str(profile.get(key, "")).strip() for key in ("contact_name", "phone", "email", "website") if str(profile.get(key, "") or "").strip())
    return [line for line in (address, contact) if line]


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

class EstimateInvoicePDF(FPDF):
    def __init__(self, profile: Dict[str, Any]):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.profile = profile
        self.accent = hex_rgb(str(profile.get("accent_color", DEFAULT_ACCENT)))
        self.set_margins(12, 10, 12)
        self.set_auto_page_break(auto=True, margin=16)
        self.set_title("Estimate or Invoice")
        self.set_author(pdf_text(profile.get("business_name", "Business")))

    def header(self) -> None:
        logo_path = str(self.profile.get("logo_path", "") or "")
        text_x = 12
        if logo_path and Path(logo_path).is_file():
            try:
                self.image(logo_path, x=12, y=7, w=23)
                text_x = 40
            except Exception:
                text_x = 12
        self.set_xy(text_x, 8)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*self.accent)
        self.cell(0, 7, pdf_text(self.profile.get("business_name", "Your Business Name")), border=0)
        self.set_text_color(45, 45, 45)
        self.set_font("Helvetica", size=8.5)
        contact_lines = profile_contact_lines(self.profile)
        self.set_xy(text_x, 16)
        for line in contact_lines[:2]:
            self.set_x(text_x)
            self.multi_cell(0, 4, pdf_text(line), border=0)
        self.set_draw_color(*self.accent)
        self.set_line_width(0.9)
        self.line(12, 30, self.w - 12, 30)
        self.set_y(34)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_draw_color(210, 215, 220)
        self.set_line_width(0.25)
        self.line(12, self.get_y(), self.w - 12, self.get_y())
        self.set_y(-10.5)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(100, 100, 100)
        name = pdf_text(self.profile.get("business_name", "Business"))
        self.cell(0, 5, f"{name}  |  Page {self.page_no()}/{{nb}}", border=0, align="C")


def section_band(pdf: EstimateInvoicePDF, x: float, y: float, width: float, title: str, height: float = 6.5) -> None:
    pdf.set_xy(x, y)
    pdf.set_fill_color(235, 238, 242)
    pdf.set_draw_color(210, 215, 220)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 2.2, y + 0.6)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(45, 50, 55)
    pdf.cell(width - 4.4, height - 1.0, pdf_text(title.upper()))


def draw_document_header(pdf: EstimateInvoicePDF, data: Dict[str, Any]) -> None:
    document_type = data.get("document_type", "Estimate")
    is_invoice = document_type == "Invoice"
    accent = pdf.accent

    pdf.set_xy(12, 34)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*accent)
    pdf.cell(98, 11, "INVOICE" if is_invoice else "ESTIMATE")

    right_x = 118
    right_w = pdf.w - 12 - right_x
    pdf.set_xy(right_x, 35)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(35, 35, 35)
    pdf.cell(right_w, 5.5, f"No. {pdf_text(data.get('doc_number', ''))}", align="R")
    pdf.set_xy(right_x, 41)
    pdf.set_font("Helvetica", size=8.5)
    pdf.cell(right_w, 4.5, f"Date: {display_date(data.get('doc_date'))}", align="R")
    deadline_label = "Due date" if is_invoice else "Valid until"
    pdf.set_xy(right_x, 46)
    pdf.cell(right_w, 4.5, f"{deadline_label}: {display_date(data.get('deadline_date'))}", align="R")

    # Bill-to panel and document metadata panel.
    left_x, panel_y, panel_w, panel_h = 12, 52, 92, 31
    right_x, right_w = 111, pdf.w - 12 - 111
    for x, width in ((left_x, panel_w), (right_x, right_w)):
        pdf.set_draw_color(210, 215, 220)
        pdf.rect(x, panel_y, width, panel_h)
    section_band(pdf, left_x, panel_y, panel_w, "Bill to")
    section_band(pdf, right_x, panel_y, right_w, "Document details")

    name = str(data.get("customer_name", "") or "").strip()
    billing_city = " ".join(str(data.get(key, "") or "").strip() for key in ("billing_city", "billing_state", "billing_zip") if str(data.get(key, "") or "").strip())
    customer_lines = [name, data.get("billing_address", ""), billing_city]
    contact = " | ".join(str(data.get(key, "")).strip() for key in ("customer_phone", "customer_email") if str(data.get(key, "") or "").strip())
    if contact:
        customer_lines.append(contact)
    y = panel_y + 8
    for index, line in enumerate(customer_lines):
        text = str(line or "").strip()
        if not text:
            continue
        pdf.set_xy(left_x + 3, y)
        pdf.set_font("Helvetica", "B" if index == 0 else "", 8.5)
        pdf.set_text_color(35, 35, 35)
        pdf.multi_cell(panel_w - 6, 4.2, pdf_text(text), border=0)
        y = pdf.get_y()

    reference = str(data.get("reference", "") or "").strip() or "-"
    meta_lines = [
        ("Document", data.get("doc_number", "")),
        ("Reference", reference),
        (deadline_label, display_date(data.get("deadline_date"))),
    ]
    y = panel_y + 8.3
    for label, value in meta_lines:
        pdf.set_xy(right_x + 3, y)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(24, 4.4, pdf_text(label + ":"))
        pdf.set_font("Helvetica", size=8)
        pdf.cell(right_w - 30, 4.4, pdf_text(value), align="R")
        y += 5.1

    # Job / work location panel.
    job_y = 86
    job_w = pdf.w - 24
    section_band(pdf, 12, job_y, job_w, "Job / service details")
    job_title = str(data.get("job_title", "") or "").strip()
    service_city = " ".join(str(data.get(key, "") or "").strip() for key in ("service_city", "service_state", "service_zip") if str(data.get(key, "") or "").strip())
    service_address = str(data.get("service_address", "") or "").strip()
    if service_city:
        service_address = f"{service_address}, {service_city}" if service_address else service_city
    site_contact = str(data.get("site_contact", "") or "").strip()
    pdf.set_xy(14, job_y + 7.5)
    pdf.set_font("Helvetica", "B", 8.7)
    pdf.set_text_color(35, 35, 35)
    pdf.cell(job_w - 4, 4.5, pdf_text(f"Job: {job_title}" if job_title else "Job: General services"))
    pdf.set_xy(14, job_y + 12)
    pdf.set_font("Helvetica", size=8.4)
    location_line = f"Service location: {service_address}" if service_address else "Service location: Same as billing address / not specified"
    if site_contact:
        location_line += f"  |  Site contact: {site_contact}"
    pdf.cell(job_w - 4, 4.5, pdf_text(location_line))


def draw_items_table_header(pdf: EstimateInvoicePDF, y: float) -> None:
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    widths = [10, 94, 18, 31, usable_w - 153]
    headers = ["#", "Description", "Qty", "Unit rate", "Amount"]
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_fill_color(*pdf.accent)
    pdf.set_text_color(255, 255, 255)
    pdf.set_draw_color(*pdf.accent)
    pdf.set_font("Helvetica", "B", 8.5)
    x = pdf.l_margin
    for title, width in zip(headers, widths):
        pdf.set_xy(x, y)
        pdf.cell(width, 7.5, pdf_text(title), border=1, align="C", fill=True)
        x += width
    pdf.set_text_color(35, 35, 35)
    pdf.set_y(y + 7.5)


def ensure_pdf_space(pdf: EstimateInvoicePDF, needed: float, repeat_table: bool = False) -> None:
    if pdf.get_y() + needed > pdf.page_break_trigger:
        pdf.add_page()
        pdf.set_y(35)
        if repeat_table:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*pdf.accent)
            pdf.cell(0, 6, "LINE ITEMS - CONTINUED")
            pdf.set_y(pdf.get_y() + 1)
            draw_items_table_header(pdf, pdf.get_y())


def draw_item_row(pdf: EstimateInvoicePDF, item: Dict[str, Any], index: int) -> None:
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    widths = [10, 94, 18, 31, usable_w - 153]
    description = f"{item['category']}: {item['description']}"
    description = pdf_text(description)
    pdf.set_font("Helvetica", size=8.5)
    text_height = pdf.multi_cell(widths[1] - 4, 4.4, description, dry_run=True, output=MethodReturnValue.HEIGHT)
    row_h = max(7.5, float(text_height) + 2.5)
    if pdf.get_y() + row_h > pdf.page_break_trigger:
        pdf.add_page()
        pdf.set_y(35)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*pdf.accent)
        pdf.cell(0, 6, "LINE ITEMS - CONTINUED")
        pdf.set_y(pdf.get_y() + 1)
        draw_items_table_header(pdf, pdf.get_y())
    x_start, y_start = pdf.l_margin, pdf.get_y()
    line_total = round(item["quantity"] * item["unit_price"], 2)
    values = [str(index), description, format_quantity(item["quantity"]), money(item["unit_price"]), money(line_total)]

    pdf.set_draw_color(215, 219, 224)
    pdf.set_line_width(0.2)
    x = x_start
    for width in widths:
        pdf.rect(x, y_start, width, row_h)
        x += width

    # Description wraps; the other fields remain vertically centered in the same row.
    pdf.set_xy(x_start + widths[0] + 2, y_start + 1.2)
    pdf.set_font("Helvetica", size=8.3)
    pdf.set_text_color(35, 35, 35)
    pdf.multi_cell(widths[1] - 4, 4.4, description, border=0)

    x = x_start
    aligns = ["C", "L", "C", "R", "R"]
    for field_index, (width, value, alignment) in enumerate(zip(widths, values, aligns)):
        if field_index == 1:
            x += width
            continue
        pdf.set_xy(x + (1 if field_index in (3, 4) else 0), y_start)
        pdf.set_font("Helvetica", size=8.3)
        pdf.set_text_color(35, 35, 35)
        pdf.cell(width - (1 if field_index in (3, 4) else 0), row_h, pdf_text(value), border=0, align=alignment)
        x += width
    pdf.set_y(y_start + row_h)


def draw_totals(pdf: EstimateInvoicePDF, data: Dict[str, Any], totals: Dict[str, float]) -> None:
    is_invoice = data.get("document_type") == "Invoice"
    left = pdf.w - pdf.r_margin - 96
    label_w, value_w = 63, 33
    rows: List[Tuple[str, str, bool]] = [
        ("Subtotal", money(totals["subtotal"]), False),
        (f"Taxable subtotal", money(totals["taxable_subtotal"]), False),
        (f"Tax ({totals['tax_rate']:.2f}%)", money(totals["tax"]), False),
    ]
    if totals["discount"] > 0:
        rows.append(("Discount", "- " + money(totals["discount"]), False))
    rows.append(("TOTAL", money(totals["total"]), True))
    rows.append(("Amount paid" if is_invoice else "Deposit / retainer", money(totals["amount_paid"]), False))
    if totals["credit"] > 0:
        rows.append(("Credit / overpayment", money(totals["credit"]), True))
    else:
        rows.append(("BALANCE DUE" if is_invoice else "ESTIMATED BALANCE", money(totals["balance_due"]), True))

    for label, value, bold in rows:
        y = pdf.get_y()
        if label in ("TOTAL", "BALANCE DUE", "ESTIMATED BALANCE", "Credit / overpayment"):
            pdf.set_fill_color(235, 238, 242)
            pdf.rect(left, y, label_w + value_w, 6.5, style="F")
        pdf.set_xy(left, y)
        pdf.set_font("Helvetica", "B" if bold else "", 8.8)
        pdf.set_text_color(35, 35, 35)
        pdf.cell(label_w, 6.5, pdf_text(label), align="R")
        pdf.set_xy(left + label_w, y)
        pdf.cell(value_w, 6.5, pdf_text(value), align="R")
        pdf.set_y(y + 6.5)


def draw_optional_text_block(pdf: EstimateInvoicePDF, heading: str, content: Any) -> None:
    text = str(content or "").strip()
    if not text:
        return
    estimated_h = pdf.multi_cell(pdf.w - 28, 4.5, pdf_text(text), dry_run=True, output=MethodReturnValue.HEIGHT)
    ensure_pdf_space(pdf, min(float(estimated_h) + 10, 35))
    section_band(pdf, 12, pdf.get_y(), pdf.w - 24, heading, height=6)
    pdf.set_xy(14, pdf.get_y() + 7)
    pdf.set_font("Helvetica", size=8.5)
    pdf.set_text_color(45, 45, 45)
    pdf.multi_cell(pdf.w - 28, 4.5, pdf_text(text), border=0)
    pdf.set_y(pdf.get_y() + 2)


def generate_pdf(data: Dict[str, Any], output_path: Path, profile: Dict[str, Any]) -> None:
    pdf = EstimateInvoicePDF(profile)
    pdf.alias_nb_pages()
    pdf.add_page()
    draw_document_header(pdf, data)

    # Item table
    items = cleaned_items(data.get("items", []))
    table_y = 108
    pdf.set_y(table_y)
    draw_items_table_header(pdf, table_y)
    if items:
        for index, item in enumerate(items, start=1):
            draw_item_row(pdf, item, index)
    else:
        draw_item_row(pdf, {"category": "Other", "description": "No line items provided", "quantity": 0, "unit_price": 0}, 1)

    totals = calculate_totals(data)
    # Keep the totals together where possible instead of splitting them over pages.
    ensure_pdf_space(pdf, 59)
    draw_totals(pdf, data, totals)

    draw_optional_text_block(pdf, "Notes", data.get("notes", ""))
    draw_optional_text_block(pdf, "Terms and conditions", data.get("terms", ""))

    if data.get("document_type") != "Invoice":
        ensure_pdf_space(pdf, 18)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(45, 45, 45)
        pdf.cell(0, 5, "ESTIMATE APPROVAL (OPTIONAL)")
        y = pdf.get_y() + 7
        pdf.set_draw_color(95, 100, 105)
        pdf.line(12, y, 112, y)
        pdf.line(125, y, pdf.w - 12, y)
        pdf.set_xy(12, y + 1)
        pdf.set_font("Helvetica", size=7.5)
        pdf.cell(100, 4, "Customer signature / approval")
        pdf.set_xy(125, y + 1)
        pdf.cell(pdf.w - 137, 4, "Date")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))


# ---------------------------------------------------------------------------
# Streamlit UI helpers
# ---------------------------------------------------------------------------


def build_document_label(filename: str, folder: Path) -> str:
    try:
        with (folder / filename).open("r", encoding="utf-8") as file:
            record = json.load(file)
        number = record.get("doc_number", Path(filename).stem)
        customer = record.get("customer_name", "Customer not entered") or "Customer not entered"
        created = display_date(record.get("doc_date"))
        return f"{number}  |  {customer}  |  {created}"
    except (OSError, json.JSONDecodeError, TypeError):
        return filename


def load_document(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("The selected saved document does not contain a valid record.")
    # Accommodate older, simpler records where possible.
    data.setdefault("document_type", "Invoice" if "invoice" in path.parent.name.lower() else "Estimate")
    data.setdefault("items", [])
    data.setdefault("tax_rate", 0.0)
    data.setdefault("discount", "")
    data.setdefault("amount_paid", data.get("paid", 0.0))
    data.setdefault("doc_date", date.today().isoformat())
    data.setdefault("deadline_date", (date.today() + timedelta(days=14)).isoformat())
    data.setdefault("terms", "")
    return data


def ensure_session_document(document_type: str) -> None:
    st.session_state["active_doc"] = new_document(document_type)
    st.session_state["active_record_path"] = ""
    st.session_state["active_form_token"] = uuid.uuid4().hex[:10]
    st.session_state["new_document_type"] = document_type


def current_form_values(data: Dict[str, Any], token: str) -> Dict[str, Any]:
    document_type = data.get("document_type", "Estimate")
    is_invoice = document_type == "Invoice"
    col1, col2, col3 = st.columns(3)
    doc_number = col1.text_input("Document number", value=str(data.get("doc_number", "")), key=f"{token}_number")
    doc_date = col2.date_input("Document date", value=date_value(data.get("doc_date"), date.today()), key=f"{token}_date")
    deadline_label = "Due date" if is_invoice else "Estimate valid until"
    deadline_date = col3.date_input(deadline_label, value=date_value(data.get("deadline_date"), date.today() + timedelta(days=14 if is_invoice else 30)), key=f"{token}_deadline")

    reference = st.text_input("Purchase order / customer reference (optional)", value=str(data.get("reference", "")), key=f"{token}_reference")

    st.subheader("Customer / billing information")
    c1, c2 = st.columns(2)
    customer_name = c1.text_input("Customer or business name *", value=str(data.get("customer_name", "")), key=f"{token}_customer_name")
    customer_phone = c2.text_input("Phone", value=str(data.get("customer_phone", "")), key=f"{token}_customer_phone")
    customer_email = c2.text_input("Email", value=str(data.get("customer_email", "")), key=f"{token}_customer_email")
    billing_address = c1.text_input("Billing street address", value=str(data.get("billing_address", "")), key=f"{token}_billing_address")
    c3, c4, c5 = st.columns([1.4, 0.7, 0.8])
    billing_city = c3.text_input("City", value=str(data.get("billing_city", "")), key=f"{token}_billing_city")
    billing_state = c4.text_input("State", value=str(data.get("billing_state", "")), key=f"{token}_billing_state")
    billing_zip = c5.text_input("ZIP", value=str(data.get("billing_zip", "")), key=f"{token}_billing_zip")

    st.subheader("Job / service location")
    job_title = st.text_input("Job title / type of work", value=str(data.get("job_title", "")), key=f"{token}_job_title", placeholder="Example: Fence repair, faucet replacement, appliance installation")
    service_address = st.text_input("Service address", value=str(data.get("service_address", "")), key=f"{token}_service_address", help="Leave blank if the work is performed at the billing address or no location is needed.")
    sc1, sc2, sc3 = st.columns([1.4, 0.7, 0.8])
    service_city = sc1.text_input("Service city", value=str(data.get("service_city", "")), key=f"{token}_service_city")
    service_state = sc2.text_input("Service state", value=str(data.get("service_state", "")), key=f"{token}_service_state")
    service_zip = sc3.text_input("Service ZIP", value=str(data.get("service_zip", "")), key=f"{token}_service_zip")
    site_contact = st.text_input("On-site contact (optional)", value=str(data.get("site_contact", "")), key=f"{token}_site_contact")

    st.subheader("Line items")
    st.caption("Add any combination of labor, parts, materials, equipment, travel, or other charges. Each line's amount is quantity × unit rate.")
    initial_items = data.get("items", [])
    if not initial_items:
        initial_items = [{"category": "Labor", "description": "", "quantity": 1.0, "unit_price": 0.0, "taxable": False}]
    rows = cleaned_items(initial_items)
    # Keep one editable blank row available for a brand-new estimate.
    if not rows:
        rows = [{"category": "Labor", "description": "", "quantity": 1.0, "unit_price": 0.0, "taxable": False}]
    edited_table = st.data_editor(
        rows,
        key=f"{token}_line_items",
        num_rows="dynamic",
        hide_index=True,
        column_order=["category", "description", "quantity", "unit_price", "taxable"],
        column_config={
            "category": st.column_config.SelectboxColumn("Type", options=CATEGORIES, required=True),
            "description": st.column_config.TextColumn("Description", width="large", required=True),
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.0, step=0.25, format="%.2f", required=True),
            "unit_price": st.column_config.NumberColumn("Unit rate ($)", min_value=0.0, step=1.0, format="$%.2f", required=True),
            "taxable": st.column_config.CheckboxColumn("Taxable?", default=False),
        },
    )
    line_items = cleaned_items(edited_table.to_dict("records") if hasattr(edited_table, "to_dict") else edited_table)

    st.subheader("Tax, discount and payments")
    tc1, tc2, tc3 = st.columns(3)
    tax_rate = tc1.number_input("Tax rate (%)", min_value=0.0, max_value=100.0, value=max(0.0, as_float(data.get("tax_rate", 0.0))), step=0.25, key=f"{token}_tax_rate")
    discount = tc2.text_input("Discount (e.g. 10% or $25)", value=str(data.get("discount", "")), key=f"{token}_discount")
    paid_label = "Amount paid" if is_invoice else "Deposit / retainer"
    amount_paid = tc3.number_input(paid_label + " ($)", min_value=0.0, value=max(0.0, as_float(data.get("amount_paid", 0.0))), step=10.0, key=f"{token}_amount_paid")

    # Live calculation preview.
    preview_data = {"items": line_items, "tax_rate": tax_rate, "discount": discount, "amount_paid": amount_paid}
    totals = calculate_totals(preview_data)
    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("Subtotal", money(totals["subtotal"]))
    pc2.metric("Tax", money(totals["tax"]))
    pc3.metric("Total", money(totals["total"]))
    pc4.metric("Balance due", money(totals["balance_due"]) if totals["credit"] == 0 else f"Credit {money(totals['credit'])}")
    st.caption("Tax is calculated on line items marked taxable, before discount. Tax rules vary by location; verify taxable items, rates, and discount treatment for your jurisdiction.")

    st.subheader("Notes and terms")
    notes = st.text_area("Notes / work description / exclusions", value=str(data.get("notes", "")), key=f"{token}_notes", height=95, placeholder="Describe the work, assumptions, exclusions, parts supplied by the customer, or other job-specific details.")
    terms = st.text_area("Terms and conditions", value=str(data.get("terms", "")), key=f"{token}_terms", height=85)

    return {
        **data,
        "document_type": document_type,
        "doc_number": safe_document_number(doc_number),
        "doc_date": doc_date.isoformat(),
        "deadline_date": deadline_date.isoformat(),
        "reference": reference.strip(),
        "customer_name": customer_name.strip(),
        "customer_phone": customer_phone.strip(),
        "customer_email": customer_email.strip(),
        "billing_address": billing_address.strip(),
        "billing_city": billing_city.strip(),
        "billing_state": billing_state.strip(),
        "billing_zip": billing_zip.strip(),
        "job_title": job_title.strip(),
        "service_address": service_address.strip(),
        "service_city": service_city.strip(),
        "service_state": service_state.strip(),
        "service_zip": service_zip.strip(),
        "site_contact": site_contact.strip(),
        "items": line_items,
        "tax_rate": tax_rate,
        "discount": discount.strip(),
        "amount_paid": amount_paid,
        "notes": notes.strip(),
        "terms": terms.strip(),
    }


def save_and_render_document(data: Dict[str, Any], profile: Dict[str, Any]) -> Optional[Path]:
    if not data.get("customer_name", "").strip():
        st.error("Enter the customer or business name before saving.")
        return None
    items = cleaned_items(data.get("items", []))
    if not items:
        st.error("Add at least one line item with a description before saving.")
        return None
    if not data.get("doc_number", "").strip():
        data["doc_number"] = next_document_number(data.get("document_type", "Estimate"))

    data["items"] = items
    folder = get_folder(data.get("document_type", "Estimate"))
    number = safe_document_number(data.get("doc_number"))
    if not number:
        st.error("The document number is invalid. Use letters, numbers, dashes, periods, or underscores.")
        return None
    data["doc_number"] = number
    json_path = folder / f"{number}.json"
    pdf_path = folder / f"{number}.pdf"
    active_record_path = Path(st.session_state.get("active_record_path", "")) if st.session_state.get("active_record_path") else None

    # Prevent silently overwriting another saved estimate or invoice.
    same_record = active_record_path is not None and active_record_path.resolve() == json_path.resolve()
    if json_path.exists() and not same_record:
        st.error(f"A document named {number} already exists. Change the document number, or load that saved document to edit it.")
        return None

    try:
        generate_pdf(data, pdf_path, profile)
        save_json_atomic(json_path, data)
        st.session_state["active_doc"] = copy.deepcopy(data)
        st.session_state["active_record_path"] = str(json_path)
        st.success(f"{data['document_type']} saved successfully.")
        st.download_button(
            "Download PDF",
            data=pdf_path.read_bytes(),
            file_name=pdf_path.name,
            mime="application/pdf",
            key=f"download_current_{st.session_state.get('active_form_token', uuid.uuid4().hex)}",
        )
        st.caption(f"Saved locally to: {pdf_path}")
        return json_path
    except Exception as error:  # Surface file/PDF errors in the UI without crashing the app.
        st.error(f"Could not save the document: {error}")
        return None


def create_document_page(profile: Dict[str, Any]) -> None:
    st.title("Estimate & Invoice Builder")
    st.caption("Create professional documents for repairs, maintenance, installations, improvements, and general service work.")

    active_doc = st.session_state.get("active_doc")
    if active_doc is None:
        default_type = st.session_state.get("new_document_type", "Estimate")
        chosen_type = st.selectbox("Document type", ["Estimate", "Invoice"], index=0 if default_type == "Estimate" else 1, key="new_document_type_picker")
        # Initialize the blank document once when there is no active record. On
        # subsequent reruns, active_doc is present and its edited values persist.
        ensure_session_document(chosen_type)
        active_doc = st.session_state.get("active_doc")
    else:
        record_path = st.session_state.get("active_record_path", "")
        if record_path:
            st.info(f"Editing saved document: {active_doc.get('doc_number', 'Untitled')} ({active_doc.get('document_type', 'Estimate')})")
        else:
            st.info(f"New {active_doc.get('document_type', 'Estimate').lower()}: {active_doc.get('doc_number', 'Untitled')} (not saved yet)")
        if st.button("Start a new document", key="start_new_document"):
            st.session_state["active_doc"] = None
            st.session_state["active_record_path"] = ""
            st.session_state["active_form_token"] = uuid.uuid4().hex[:10]
            st.session_state["new_document_type"] = "Estimate"
            st.session_state["new_document_type_picker"] = "Estimate"
            st.rerun()

    if not active_doc:
        return
    token = st.session_state.get("active_form_token", "form")
    with st.container(border=True):
        form_data = current_form_values(active_doc, token)
        left, right = st.columns([1, 1])
        with left:
            if st.button("Save & generate PDF", type="primary", key=f"{token}_save"):
                save_and_render_document(form_data, profile)
        with right:
            existing_path = Path(st.session_state.get("active_record_path", "")) if st.session_state.get("active_record_path") else None
            if existing_path and existing_path.with_suffix(".pdf").exists():
                st.download_button(
                    "Download last saved PDF",
                    data=existing_path.with_suffix(".pdf").read_bytes(),
                    file_name=existing_path.with_suffix(".pdf").name,
                    mime="application/pdf",
                    key=f"{token}_download_last",
                )


def saved_documents_page() -> None:
    st.title("Saved documents")
    kind = st.selectbox("Document type", ["Estimate", "Invoice"], key="saved_document_type")
    folder = get_folder(kind)
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        st.info(f"No saved {kind.lower()}s found yet.")
        return
    selected_name = st.selectbox(
        "Choose a document",
        [path.name for path in files],
        format_func=lambda name: build_document_label(name, folder),
        key="saved_document_pick",
    )
    selected_json = folder / selected_name
    selected_pdf = selected_json.with_suffix(".pdf")
    try:
        document = load_document(selected_json)
        totals = calculate_totals(document)
        c1, c2, c3 = st.columns(3)
        c1.metric("Customer", document.get("customer_name") or "Not entered")
        c2.metric("Document total", money(totals["total"]))
        c3.metric("Balance due", money(totals["balance_due"]) if totals["credit"] == 0 else f"Credit {money(totals['credit'])}")
        if selected_pdf.exists():
            st.download_button("Download PDF", data=selected_pdf.read_bytes(), file_name=selected_pdf.name, mime="application/pdf", key=f"saved_pdf_{selected_name}")
        else:
            st.warning("The saved record has no PDF yet. Load and save it to generate one.")
        if st.button("Load this document for editing", type="primary", key=f"edit_{selected_name}"):
            st.session_state["active_doc"] = document
            st.session_state["active_record_path"] = str(selected_json)
            st.session_state["active_form_token"] = uuid.uuid4().hex[:10]
            st.session_state["new_document_type"] = document.get("document_type", kind)
            # Main applies this navigation request before rendering the sidebar radio,
            # avoiding a write to an already-instantiated Streamlit widget key.
            st.session_state["pending_workspace"] = "Create Document"
            st.rerun()
        with st.expander("Document details"):
            st.write(f"**Number:** {document.get('doc_number', selected_json.stem)}")
            st.write(f"**Date:** {display_date(document.get('doc_date'))}")
            st.write(f"**Job:** {document.get('job_title', '')}")
            st.write(f"**Saved record:** `{selected_json}`")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        st.error(f"Could not read this saved document: {error}")


def business_settings_page() -> None:
    st.title("Business profile and appearance")
    st.caption("These details are printed at the top of estimates and invoices. Fill in only the fields you want customers to see.")
    profile = load_profile()
    with st.form("business_profile_form"):
        business_name = st.text_input("Business name", value=str(profile.get("business_name", "")))
        contact_name = st.text_input("Contact / owner name (optional)", value=str(profile.get("contact_name", "")))
        c1, c2 = st.columns(2)
        phone = c1.text_input("Business phone", value=str(profile.get("phone", "")))
        email = c2.text_input("Business email", value=str(profile.get("email", "")))
        website = st.text_input("Website (optional)", value=str(profile.get("website", "")))
        address = st.text_input("Street address", value=str(profile.get("address", "")))
        c3, c4, c5 = st.columns([1.4, 0.7, 0.8])
        city = c3.text_input("City", value=str(profile.get("city", "")))
        state = c4.text_input("State", value=str(profile.get("state", "")))
        zip_code = c5.text_input("ZIP", value=str(profile.get("zip", "")))
        accent_color = st.color_picker("Accent color", value=str(profile.get("accent_color", DEFAULT_ACCENT)))
        uploaded_logo = st.file_uploader("Business logo (optional PNG or JPG)", type=["png", "jpg", "jpeg"])
        remove_logo = st.checkbox("Remove the currently saved logo")
        submitted = st.form_submit_button("Save business profile", type="primary")

    if submitted:
        profile.update({
            "business_name": business_name.strip() or "Your Business Name",
            "contact_name": contact_name.strip(),
            "phone": phone.strip(),
            "email": email.strip(),
            "website": website.strip(),
            "address": address.strip(),
            "city": city.strip(),
            "state": state.strip(),
            "zip": zip_code.strip(),
            "accent_color": accent_color,
        })
        if remove_logo:
            old_logo = Path(str(profile.get("logo_path", ""))) if profile.get("logo_path") else None
            if old_logo and old_logo.is_file():
                try:
                    old_logo.unlink()
                except OSError:
                    pass
            profile["logo_path"] = ""
        if uploaded_logo is not None:
            suffix = ".jpg" if uploaded_logo.name.lower().endswith((".jpg", ".jpeg")) else ".png"
            logo_path = ASSET_DIR / f"business_logo{suffix}"
            logo_path.write_bytes(uploaded_logo.getbuffer())
            profile["logo_path"] = str(logo_path)
        save_json_atomic(PROFILE_PATH, profile)
        st.success("Business profile saved. New PDFs will use these settings.")
        st.rerun()

    logo_path = Path(str(profile.get("logo_path", ""))) if profile.get("logo_path") else None
    if logo_path and logo_path.is_file():
        st.image(str(logo_path), caption="Currently saved business logo", width=220)
    st.caption(f"Document storage folder: {DATA_ROOT}")


def main() -> None:
    st.set_page_config(page_title="General Estimates & Invoices", page_icon="🧰", layout="wide")
    ensure_directories()
    pending_workspace = st.session_state.pop("pending_workspace", None)
    if pending_workspace in {"Create Document", "Saved Documents", "Business Settings"}:
        st.session_state["workspace"] = pending_workspace
    st.sidebar.title("🧰 Service Document Toolkit")
    st.sidebar.caption("For general repair, maintenance, and handyman work")
    workspace = st.sidebar.radio("Workspace", ["Create Document", "Saved Documents", "Business Settings"], key="workspace")
    profile = load_profile()
    if workspace == "Create Document":
        create_document_page(profile)
    elif workspace == "Saved Documents":
        saved_documents_page()
    else:
        business_settings_page()
    st.sidebar.divider()
    st.sidebar.caption("Saved documents stay on this computer unless you move or share them.")


if __name__ == "__main__":
    main()
