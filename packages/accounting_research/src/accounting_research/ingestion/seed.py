"""Generate and load a synthetic accounting dataset into Postgres.

Creates ~10 related tables (schema in test_database_resources/schema.sql) with
provenance columns, rich enough to exercise table selection and the three core
question types:
  1. multi-year aggregation  (travel spend by Finance over the last 3 years)
  2. trend                    (monthly spending since the start of 2026)
  3. status / exception       (audit cases not audited yet)

The table SCHEMA lives in schema.sql; this module owns the data-generation
logic and its reference lists.

Run:  python -m accounting_research.ingestion.seed   (or: ar-seed)
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from ..core.db import admin_conn
from ..core.settings import get_settings

RNG = random.Random(42)

DEPARTMENTS = ["Finance", "Engineering", "Sales", "HR", "Operations", "Legal", "Marketing"]

ROLES = ["Analyst", "Manager", "Director", "Associate", "Specialist", "VP"]

VENDORS = [
    ("Skyline Travel Co", "Travel Agency"),
    ("Globetrotter Bookings", "Travel Agency"),
    ("Atlas Airfare", "Travel Agency"),
    ("CloudWorks SaaS", "Software"),
    ("DataForge Analytics", "Software"),
    ("LedgerPro Systems", "Software"),
    ("Beacon Consulting", "Consulting"),
    ("Meridian Advisory", "Consulting"),
    ("FreshBite Catering", "Catering"),
    ("Corner Cafe Meals", "Catering"),
    ("OfficeHub Supplies", "Office Supplies"),
    ("PaperTrail Stationery", "Office Supplies"),
    ("BrightMind Training", "Training"),
    ("SummitPrint Services", "Office Supplies"),
    ("NorthStar Legal Svcs", "Consulting"),
]

EXPENSE_CATEGORIES = ["Travel", "Meals", "Software", "Training", "Office Supplies", "Consulting"]
CATEGORY_WEIGHTS = [30, 20, 15, 10, 15, 10]
CATEGORY_RANGE = {
    "Travel": (200, 4500),
    "Meals": (15, 180),
    "Software": (50, 2000),
    "Training": (150, 3000),
    "Office Supplies": (10, 500),
    "Consulting": (500, 15000),
}

FIRST_NAMES = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mason", "Sophia", "Lucas",
               "Mia", "Ethan", "Isabella", "Logan", "Amir", "Priya", "Wei", "Sofia",
               "Diego", "Hana", "Omar", "Nina", "Raj", "Chloe", "Marco", "Yuki"]
LAST_NAMES = ["Smith", "Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Rossi",
              "Khan", "Silva", "Chen", "Brown", "Lopez", "Haddad", "Novak",
              "Okafor", "Muller", "Costa", "Tanaka"]


def _d(start: date, end: date) -> date:
    return start + timedelta(days=RNG.randint(0, (end - start).days))


def _prov(source_file: str, source_type: str, row: int) -> tuple[str, str, str]:
    if source_type == "excel":
        locator = f"Sheet1!A{row + 1}"
    elif source_type == "pdf":
        locator = f"page {1 + row % 3}"
    else:
        locator = f"region {row}"
    return source_file, source_type, locator


def generate() -> None:
    today = date.today()
    schema_ddl = get_settings().schema_sql.read_text(encoding="utf-8")

    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute(schema_ddl)

        # departments
        depts = []
        for i, name in enumerate(DEPARTMENTS, start=1):
            depts.append((i, name, *_prov("org_structure.xlsx", "excel", i)))
        cur.executemany(
            "INSERT INTO departments (dept_id, dept_name, source_file, source_type, locator)"
            " VALUES (%s,%s,%s,%s,%s)",
            depts,
        )
        dept_ids = [d[0] for d in depts]

        # employees
        emps = []
        for i in range(1, 41):
            name = f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"
            dept_id = RNG.choice(dept_ids)
            role = RNG.choice(ROLES)
            hire = _d(date(2018, 1, 1), date(2025, 6, 1))
            emps.append((i, name, dept_id, role, hire, *_prov("hr_roster.xlsx", "excel", i)))
        cur.executemany(
            "INSERT INTO employees (emp_id, full_name, dept_id, role, hire_date,"
            " source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            emps,
        )
        emp_rows = [(e[0], e[2]) for e in emps]  # (emp_id, dept_id)

        # vendors
        vend = []
        for i, (vname, vcat) in enumerate(VENDORS, start=1):
            vend.append((i, vname, vcat, *_prov("vendor_master.xlsx", "excel", i)))
        cur.executemany(
            "INSERT INTO vendors (vendor_id, vendor_name, vendor_category,"
            " source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s)",
            vend,
        )
        vendor_by_cat: dict[str, list[int]] = {}
        for v in vend:
            vendor_by_cat.setdefault(v[2], []).append(v[0])

        finance_dept_id = DEPARTMENTS.index("Finance") + 1

        # expenses (main table): 2023-01 .. today
        exp = []
        exp_start = date(2023, 1, 1)
        for i in range(1, 901):
            emp_id, dept_id = RNG.choice(emp_rows)
            category = RNG.choices(EXPENSE_CATEGORIES, weights=CATEGORY_WEIGHTS)[0]
            lo, hi = CATEGORY_RANGE[category]
            amount = round(RNG.uniform(lo, hi), 2)
            edate = _d(exp_start, today)
            vcat = {
                "Travel": "Travel Agency",
                "Software": "Software",
                "Consulting": "Consulting",
                "Meals": "Catering",
                "Office Supplies": "Office Supplies",
                "Training": "Training",
            }[category]
            vendor_id = RNG.choice(vendor_by_cat.get(vcat, [None])) if vcat in vendor_by_cat else None
            desc = f"{category} expense"
            year = edate.year
            exp.append((
                i, emp_id, dept_id, category, amount, "USD", edate, desc, vendor_id,
                *_prov(f"expenses_{year}.xlsx", "excel", i),
            ))
        cur.executemany(
            "INSERT INTO expenses (expense_id, emp_id, dept_id, category, amount, currency,"
            " expense_date, description, vendor_id, source_file, source_type, locator)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            exp,
        )

        # Guarantee Finance travel coverage across the last 3 years so the
        # multi-year question always has data.
        extra_id = 901
        extra = []
        for yr in (today.year - 2, today.year - 1, today.year):
            for _ in range(8):
                emp_id = RNG.choice([e[0] for e in emp_rows if e[1] == finance_dept_id]
                                    or [emp_rows[0][0]])
                amount = round(RNG.uniform(300, 4000), 2)
                edate = _d(date(yr, 1, 1), min(date(yr, 12, 31), today))
                extra.append((
                    extra_id, emp_id, finance_dept_id, "Travel", amount, "USD", edate,
                    "Finance team travel", RNG.choice(vendor_by_cat["Travel Agency"]),
                    *_prov(f"expenses_{yr}.xlsx", "excel", extra_id),
                ))
                extra_id += 1
        cur.executemany(
            "INSERT INTO expenses (expense_id, emp_id, dept_id, category, amount, currency,"
            " expense_date, description, vendor_id, source_file, source_type, locator)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            extra,
        )

        # invoices
        inv = []
        all_vendor_ids = [v[0] for v in vend]
        for i in range(1, 221):
            idate = _d(date(2024, 1, 1), today)
            due = idate + timedelta(days=30)
            status = "paid" if due < today and RNG.random() < 0.8 else \
                ("overdue" if due < today else "unpaid")
            src_type = "pdf" if RNG.random() < 0.5 else "excel"
            src_file = "invoices_scanned.pdf" if src_type == "pdf" else "ap_invoices.xlsx"
            inv.append((
                i, RNG.choice(all_vendor_ids), round(RNG.uniform(100, 20000), 2),
                idate, due, status, *_prov(src_file, src_type, i),
            ))
        cur.executemany(
            "INSERT INTO invoices (invoice_id, vendor_id, amount, invoice_date, due_date,"
            " status, source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            inv,
        )

        # audit_cases (status / exception question)
        entities = ["Travel Reimbursements", "Vendor Payments", "Payroll", "Revenue Recognition",
                    "Fixed Assets", "Procurement", "Cash Handling", "Expense Reports",
                    "Intercompany", "Tax Provisions"]
        auditors = ["A. Rahman", "J. Park", "M. Silva", "K. Owens", "L. Ferreira"]
        audit = []
        cid = 1
        for yr in (2023, 2024, 2025, 2026):
            for _ in range(15):
                status = RNG.choices(
                    ["not_audited", "in_progress", "audited"], weights=[35, 25, 40]
                )[0]
                opened = _d(date(yr, 1, 1), min(date(yr, 12, 31), today))
                closed = (opened + timedelta(days=RNG.randint(20, 120))
                          if status == "audited" else None)
                assigned = None if status == "not_audited" else RNG.choice(auditors)
                audit.append((
                    cid, RNG.choice(entities), yr, status, assigned, opened, closed,
                    *_prov("audit_tracker.xlsx", "excel", cid),
                ))
                cid += 1
        cur.executemany(
            "INSERT INTO audit_cases (case_id, entity, fiscal_year, audit_status, assigned_to,"
            " opened_date, closed_date, source_file, source_type, locator)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            audit,
        )

        # budgets
        bud = []
        bid = 1
        for dept_id in dept_ids:
            for yr in (2024, 2025, 2026):
                for cat in EXPENSE_CATEGORIES:
                    bud.append((
                        bid, dept_id, yr, cat, round(RNG.uniform(20000, 200000), 2),
                        *_prov("annual_budget.xlsx", "excel", bid),
                    ))
                    bid += 1
        cur.executemany(
            "INSERT INTO budgets (budget_id, dept_id, fiscal_year, category, budget_amount,"
            " source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            bud,
        )

        # payroll (monthly, 2025-01 .. current month)
        pay = []
        pid = 1
        months = []
        m = date(2025, 1, 1)
        while m <= today.replace(day=1):
            months.append(m)
            m = (m.replace(day=28) + timedelta(days=7)).replace(day=1)
        for emp_id, dept_id in emp_rows:
            base = RNG.uniform(4000, 12000)
            for pm in months:
                gross = round(base + RNG.uniform(-200, 200), 2)
                net = round(gross * 0.72, 2)
                pay.append((
                    pid, emp_id, dept_id, pm, gross, net,
                    *_prov("payroll_register.xlsx", "excel", pid),
                ))
                pid += 1
        cur.executemany(
            "INSERT INTO payroll (payroll_id, emp_id, dept_id, pay_period, gross_pay, net_pay,"
            " source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            pay,
        )

        # purchase_orders
        pos = []
        for i in range(1, 161):
            odate = _d(date(2024, 1, 1), today)
            status = RNG.choices(["open", "received", "cancelled"], weights=[25, 65, 10])[0]
            pos.append((
                i, RNG.choice(all_vendor_ids), RNG.choice(dept_ids),
                round(RNG.uniform(200, 30000), 2), odate, status,
                *_prov("purchase_orders.xlsx", "excel", i),
            ))
        cur.executemany(
            "INSERT INTO purchase_orders (po_id, vendor_id, dept_id, amount, order_date, status,"
            " source_file, source_type, locator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            pos,
        )

        # office_assets
        asset_types = ["Laptop", "Monitor", "Desk", "Chair", "Printer"]
        assets = []
        for i in range(1, 121):
            assets.append((
                i, RNG.choice(dept_ids), RNG.choice(asset_types),
                round(RNG.uniform(80, 3000), 2), _d(date(2020, 1, 1), today),
                *_prov("asset_register.xlsx", "excel", i),
            ))
        cur.executemany(
            "INSERT INTO office_assets (asset_id, dept_id, asset_type, purchase_cost,"
            " purchase_date, source_file, source_type, locator)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            assets,
        )

        # Ensure the read-only agent role can read every seeded table.
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO ar_readonly")

        counts = {}
        for tbl in ("departments", "employees", "vendors", "expenses", "invoices",
                    "audit_cases", "budgets", "payroll", "purchase_orders", "office_assets"):
            cur.execute(f"SELECT count(*) FROM {tbl}")
            counts[tbl] = cur.fetchone()[0]

    print(f"[{datetime.now():%H:%M:%S}] Seed complete. Row counts:")
    for tbl, n in counts.items():
        print(f"  {tbl:<16} {n:>6}")


def main() -> None:
    generate()


if __name__ == "__main__":
    main()
