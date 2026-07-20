-- Seed table schema for the demo/test accounting database.
-- Loaded by the ingestion seeder (accounting_research.ingestion.seed) before
-- inserting synthetic rows. Every table carries provenance columns
-- (source_file, source_type, locator, ingested_at) per the TDD provenance model.

DROP TABLE IF EXISTS payroll, purchase_orders, office_assets, budgets, invoices,
    audit_cases, expenses, employees, vendors, departments CASCADE;

CREATE TABLE departments (
    dept_id     int PRIMARY KEY,
    dept_name   text NOT NULL,
    source_file text NOT NULL,
    source_type text NOT NULL,
    locator     text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employees (
    emp_id      int PRIMARY KEY,
    full_name   text NOT NULL,
    dept_id     int NOT NULL REFERENCES departments(dept_id),
    role        text NOT NULL,
    hire_date   date NOT NULL,
    source_file text NOT NULL,
    source_type text NOT NULL,
    locator     text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vendors (
    vendor_id       int PRIMARY KEY,
    vendor_name     text NOT NULL,
    vendor_category text NOT NULL,
    source_file     text NOT NULL,
    source_type     text NOT NULL,
    locator         text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE expenses (
    expense_id   int PRIMARY KEY,
    emp_id       int NOT NULL REFERENCES employees(emp_id),
    dept_id      int NOT NULL REFERENCES departments(dept_id),
    category     text NOT NULL,
    amount       numeric(12,2) NOT NULL,
    currency     text NOT NULL,
    expense_date date NOT NULL,
    description  text,
    vendor_id    int REFERENCES vendors(vendor_id),
    source_file  text NOT NULL,
    source_type  text NOT NULL,
    locator      text NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE invoices (
    invoice_id   int PRIMARY KEY,
    vendor_id    int NOT NULL REFERENCES vendors(vendor_id),
    amount       numeric(12,2) NOT NULL,
    invoice_date date NOT NULL,
    due_date     date NOT NULL,
    status       text NOT NULL,
    source_file  text NOT NULL,
    source_type  text NOT NULL,
    locator      text NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_cases (
    case_id      int PRIMARY KEY,
    entity       text NOT NULL,
    fiscal_year  int NOT NULL,
    audit_status text NOT NULL,
    assigned_to  text,
    opened_date  date NOT NULL,
    closed_date  date,
    source_file  text NOT NULL,
    source_type  text NOT NULL,
    locator      text NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE budgets (
    budget_id     int PRIMARY KEY,
    dept_id       int NOT NULL REFERENCES departments(dept_id),
    fiscal_year   int NOT NULL,
    category      text NOT NULL,
    budget_amount numeric(12,2) NOT NULL,
    source_file   text NOT NULL,
    source_type   text NOT NULL,
    locator       text NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payroll (
    payroll_id  int PRIMARY KEY,
    emp_id      int NOT NULL REFERENCES employees(emp_id),
    dept_id     int NOT NULL REFERENCES departments(dept_id),
    pay_period  date NOT NULL,
    gross_pay   numeric(12,2) NOT NULL,
    net_pay     numeric(12,2) NOT NULL,
    source_file text NOT NULL,
    source_type text NOT NULL,
    locator     text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE purchase_orders (
    po_id       int PRIMARY KEY,
    vendor_id   int NOT NULL REFERENCES vendors(vendor_id),
    dept_id     int NOT NULL REFERENCES departments(dept_id),
    amount      numeric(12,2) NOT NULL,
    order_date  date NOT NULL,
    status      text NOT NULL,
    source_file text NOT NULL,
    source_type text NOT NULL,
    locator     text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE office_assets (
    asset_id      int PRIMARY KEY,
    dept_id       int NOT NULL REFERENCES departments(dept_id),
    asset_type    text NOT NULL,
    purchase_cost numeric(12,2) NOT NULL,
    purchase_date date NOT NULL,
    source_file   text NOT NULL,
    source_type   text NOT NULL,
    locator       text NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now()
);
