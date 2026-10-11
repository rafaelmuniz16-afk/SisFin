-- PostgreSQL 16. Snapshot V1. Use Alembic OU este DDL, não os dois.
BEGIN;

CREATE TABLE families (
	name VARCHAR(120) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	timezone VARCHAR(64) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_families PRIMARY KEY (id), 
	CONSTRAINT ck_families_single_currency CHECK (currency = 'BRL')
);

CREATE TABLE users (
	email VARCHAR(320) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	password_hash VARCHAR(100) NOT NULL, 
	active BOOLEAN NOT NULL, 
	token_version INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_lower_email CHECK (email = lower(email)), 
	CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE audit_logs (
	actor_id UUID, 
	table_name VARCHAR(80) NOT NULL, 
	row_key VARCHAR(160) NOT NULL, 
	action VARCHAR(10) NOT NULL, 
	old_value JSONB, 
	new_value JSONB, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
	CONSTRAINT uq_audit_logs_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_audit_logs_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
	CONSTRAINT fk_audit_logs_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE categories (
	name VARCHAR(120) NOT NULL, 
	parent_id UUID, 
	kind VARCHAR(10) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_categories PRIMARY KEY (id), 
	CONSTRAINT fk_categories_family_id_parent_id_categories FOREIGN KEY(family_id, parent_id) REFERENCES categories (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_categories_kind CHECK (kind IN ('income', 'expense')), 
	CONSTRAINT ck_categories_not_self CHECK (parent_id IS NULL OR parent_id <> id), 
	CONSTRAINT uq_categories_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_categories_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE family_members (
	family_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	role VARCHAR(6) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_family_members PRIMARY KEY (family_id, user_id), 
	CONSTRAINT fk_family_members_family_id_families FOREIGN KEY(family_id) REFERENCES families (id), 
	CONSTRAINT fk_family_members_user_id_users FOREIGN KEY(user_id) REFERENCES users (id), 
	CONSTRAINT ck_family_members_role CHECK (role IN ('admin', 'editor', 'viewer'))
);

CREATE TABLE idempotency_records (
	key VARCHAR(128) NOT NULL, 
	actor_id UUID NOT NULL, 
	operation VARCHAR(240) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	response JSONB NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_idempotency_records PRIMARY KEY (id), 
	CONSTRAINT uq_idempotency_records_family_id_key UNIQUE (family_id, key), 
	CONSTRAINT uq_idempotency_records_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_idempotency_records_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
	CONSTRAINT fk_idempotency_records_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE ledger_accounts (
	name VARCHAR(160) NOT NULL, 
	kind VARCHAR(9) NOT NULL, 
	system_key VARCHAR(50), 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_ledger_accounts PRIMARY KEY (id), 
	CONSTRAINT uq_ledger_accounts_family_id_system_key UNIQUE (family_id, system_key), 
	CONSTRAINT uq_ledger_accounts_family_id UNIQUE (family_id, id), 
	CONSTRAINT ck_ledger_accounts_ledgerkind CHECK (kind IN ('asset', 'liability', 'equity', 'income', 'expense')), 
	CONSTRAINT fk_ledger_accounts_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE accounts (
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	ledger_account_id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	bank VARCHAR(120), 
	active BOOLEAN NOT NULL, 
	allow_overdraft BOOLEAN NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_accounts PRIMARY KEY (id), 
	CONSTRAINT fk_accounts_family_id_ledger_account_id_ledger_accounts FOREIGN KEY(family_id, ledger_account_id) REFERENCES ledger_accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_accounts_family_id_owner_id_family_members FOREIGN KEY(family_id, owner_id) REFERENCES family_members (family_id, user_id) ON DELETE RESTRICT, 
	CONSTRAINT uq_accounts_family_id UNIQUE (family_id, id), 
	CONSTRAINT ck_accounts_accountkind CHECK (kind IN ('checking', 'savings', 'cash', 'investment', 'wallet')), 
	CONSTRAINT uq_accounts_ledger_account_id UNIQUE (ledger_account_id), 
	CONSTRAINT fk_accounts_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE budgets (
	category_id UUID NOT NULL, 
	month DATE NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	rollover BOOLEAN NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_budgets PRIMARY KEY (id), 
	CONSTRAINT fk_budgets_family_id_category_id_categories FOREIGN KEY(family_id, category_id) REFERENCES categories (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_budgets_values CHECK (amount >= 0 AND EXTRACT(DAY FROM month)=1), 
	CONSTRAINT uq_budgets_family_id_category_id_month UNIQUE (family_id, category_id, month), 
	CONSTRAINT uq_budgets_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_budgets_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE wealth_positions (
	name VARCHAR(160) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	ledger_account_id UUID NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_wealth_positions PRIMARY KEY (id), 
	CONSTRAINT fk_wealth_positions_family_id_ledger_account_id_ledger_accounts FOREIGN KEY(family_id, ledger_account_id) REFERENCES ledger_accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_wealth_positions_kind CHECK (kind IN ('asset','liability')), 
	CONSTRAINT uq_wealth_positions_family_id UNIQUE (family_id, id), 
	CONSTRAINT uq_wealth_positions_ledger_account_id UNIQUE (ledger_account_id), 
	CONSTRAINT fk_wealth_positions_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE credit_cards (
	name VARCHAR(120) NOT NULL, 
	ledger_account_id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	payment_account_id UUID NOT NULL, 
	credit_limit NUMERIC(12, 2) NOT NULL, 
	closing_day INTEGER NOT NULL, 
	due_day INTEGER NOT NULL, 
	last_four VARCHAR(4), 
	active BOOLEAN NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_credit_cards PRIMARY KEY (id), 
	CONSTRAINT fk_credit_cards_family_id_ledger_account_id_ledger_accounts FOREIGN KEY(family_id, ledger_account_id) REFERENCES ledger_accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_credit_cards_family_id_payment_account_id_accounts FOREIGN KEY(family_id, payment_account_id) REFERENCES accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_credit_cards_family_id_owner_id_family_members FOREIGN KEY(family_id, owner_id) REFERENCES family_members (family_id, user_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_credit_cards_positive_limit CHECK (credit_limit >= 0), 
	CONSTRAINT ck_credit_cards_days CHECK (closing_day BETWEEN 1 AND 31 AND due_day BETWEEN 1 AND 31), 
	CONSTRAINT ck_credit_cards_last_four CHECK (last_four IS NULL OR last_four ~ '^[0-9]{4}$'), 
	CONSTRAINT uq_credit_cards_family_id UNIQUE (family_id, id), 
	CONSTRAINT uq_credit_cards_ledger_account_id UNIQUE (ledger_account_id), 
	CONSTRAINT fk_credit_cards_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE goals (
	name VARCHAR(160) NOT NULL, 
	account_id UUID NOT NULL, 
	target_amount NUMERIC(12, 2) NOT NULL, 
	target_date DATE NOT NULL, 
	monthly_contribution NUMERIC(12, 2) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_goals PRIMARY KEY (id), 
	CONSTRAINT fk_goals_family_id_account_id_accounts FOREIGN KEY(family_id, account_id) REFERENCES accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_goals_values CHECK (target_amount > 0 AND monthly_contribution >= 0), 
	CONSTRAINT uq_goals_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_goals_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE goal_contributions (
	goal_id UUID NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	date DATE NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_goal_contributions PRIMARY KEY (id), 
	CONSTRAINT fk_goal_contributions_family_id_goal_id_goals FOREIGN KEY(family_id, goal_id) REFERENCES goals (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_goal_contributions_nonzero CHECK (amount <> 0), 
	CONSTRAINT uq_goal_contributions_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_goal_contributions_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE invoices (
	card_id UUID NOT NULL, 
	cycle_month DATE NOT NULL, 
	closing_date DATE NOT NULL, 
	due_date DATE NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_invoices PRIMARY KEY (id), 
	CONSTRAINT fk_invoices_family_id_card_id_credit_cards FOREIGN KEY(family_id, card_id) REFERENCES credit_cards (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_invoices_family_id_card_id_cycle_month UNIQUE (family_id, card_id, cycle_month), 
	CONSTRAINT ck_invoices_month_first CHECK (EXTRACT(DAY FROM cycle_month) = 1), 
	CONSTRAINT ck_invoices_closing_in_cycle CHECK (closing_date >= cycle_month AND closing_date < cycle_month + interval '1 month'), 
	CONSTRAINT ck_invoices_due_after_close CHECK (due_date > closing_date), 
	CONSTRAINT uq_invoices_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_invoices_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE recurrences (
	name VARCHAR(160) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	variable BOOLEAN NOT NULL, 
	subscription BOOLEAN NOT NULL, 
	frequency VARCHAR(11) NOT NULL, 
	interval INTEGER NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE, 
	category_id UUID NOT NULL, 
	account_id UUID, 
	card_id UUID, 
	payment_method VARCHAR(15) NOT NULL, 
	active BOOLEAN NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_recurrences PRIMARY KEY (id), 
	CONSTRAINT fk_recurrences_family_id_category_id_categories FOREIGN KEY(family_id, category_id) REFERENCES categories (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_recurrences_family_id_account_id_accounts FOREIGN KEY(family_id, account_id) REFERENCES accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_recurrences_family_id_card_id_credit_cards FOREIGN KEY(family_id, card_id) REFERENCES credit_cards (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_recurrences_values CHECK (amount > 0 AND interval BETWEEN 1 AND 120), 
	CONSTRAINT ck_recurrences_dates CHECK (end_date IS NULL OR end_date >= start_date), 
	CONSTRAINT ck_recurrences_kind CHECK (kind IN ('income','expense')), 
	CONSTRAINT ck_recurrences_funding CHECK ((card_id IS NOT NULL AND account_id IS NULL AND kind='expense' AND payment_method='credit') OR (account_id IS NOT NULL AND card_id IS NULL AND payment_method<>'credit')), 
	CONSTRAINT uq_recurrences_family_id UNIQUE (family_id, id), 
	CONSTRAINT ck_recurrences_frequency CHECK (frequency IN ('daily', 'weekly', 'fortnightly', 'monthly', 'yearly')), 
	CONSTRAINT ck_recurrences_paymentmethod CHECK (payment_method IN ('pix', 'credit', 'debit', 'boleto', 'cash', 'bank_transfer', 'automatic_debit', 'wallet', 'ted', 'other')), 
	CONSTRAINT fk_recurrences_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE transactions (
	kind VARCHAR(13) NOT NULL, 
	node VARCHAR(11) NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	description VARCHAR(240) NOT NULL, 
	merchant VARCHAR(160), 
	tags VARCHAR(64)[] NOT NULL, 
	details JSONB NOT NULL, 
	competency_date DATE NOT NULL, 
	purchase_date DATE NOT NULL, 
	billing_date DATE, 
	due_date DATE, 
	paid_date DATE, 
	payment_method VARCHAR(15) NOT NULL, 
	account_id UUID, 
	destination_account_id UUID, 
	card_id UUID, 
	invoice_id UUID, 
	category_id UUID, 
	parent_id UUID, 
	related_transaction_id UUID, 
	recurrence_id UUID, 
	occurrence_date DATE, 
	installment_number INTEGER, 
	installment_count INTEGER, 
	created_by UUID NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_transactions PRIMARY KEY (id), 
	CONSTRAINT fk_transactions_family_id_account_id_accounts FOREIGN KEY(family_id, account_id) REFERENCES accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_destination_account_id_accounts FOREIGN KEY(family_id, destination_account_id) REFERENCES accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_card_id_credit_cards FOREIGN KEY(family_id, card_id) REFERENCES credit_cards (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_invoice_id_invoices FOREIGN KEY(family_id, invoice_id) REFERENCES invoices (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_category_id_categories FOREIGN KEY(family_id, category_id) REFERENCES categories (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_parent_id_transactions FOREIGN KEY(family_id, parent_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_related_transaction_id_transactions FOREIGN KEY(family_id, related_transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transactions_family_id_recurrence_id_recurrences FOREIGN KEY(family_id, recurrence_id) REFERENCES recurrences (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_transactions_positive_amount CHECK (amount > 0), 
	CONSTRAINT ck_transactions_node_parent CHECK ((node='root' AND parent_id IS NULL) OR (node<>'root' AND parent_id IS NOT NULL)), 
	CONSTRAINT ck_transactions_installment CHECK ((node='installment' AND invoice_id IS NOT NULL AND card_id IS NOT NULL AND installment_number IS NOT NULL AND installment_count IS NOT NULL AND installment_count BETWEEN 1 AND 600 AND installment_number BETWEEN 1 AND installment_count) OR (node<>'installment' AND installment_number IS NULL)), 
	CONSTRAINT ck_transactions_split_category CHECK (node<>'split' OR category_id IS NOT NULL), 
	CONSTRAINT ck_transactions_credit_purchase_count CHECK (NOT (node='root' AND kind='expense' AND card_id IS NOT NULL AND status='posted') OR (installment_count IS NOT NULL AND installment_count BETWEEN 1 AND 600)), 
	CONSTRAINT ck_transactions_occurrence_pair CHECK ((recurrence_id IS NULL) = (occurrence_date IS NULL)), 
	CONSTRAINT uq_transactions_family_id_parent_id_installment_number UNIQUE (family_id, parent_id, installment_number), 
	CONSTRAINT uq_transactions_family_id_recurrence_id_occurrence_date UNIQUE (family_id, recurrence_id, occurrence_date), 
	CONSTRAINT uq_transactions_family_id UNIQUE (family_id, id), 
	CONSTRAINT ck_transactions_transactionkind CHECK (kind IN ('income', 'expense', 'transfer', 'reimbursement', 'reversal', 'adjustment')), 
	CONSTRAINT ck_transactions_nodekind CHECK (node IN ('root', 'installment', 'split')), 
	CONSTRAINT ck_transactions_transactionstatus CHECK (status IN ('planned', 'posted', 'cancelled')), 
	CONSTRAINT ck_transactions_paymentmethod CHECK (payment_method IN ('pix', 'credit', 'debit', 'boleto', 'cash', 'bank_transfer', 'automatic_debit', 'wallet', 'ted', 'other')), 
	CONSTRAINT fk_transactions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id), 
	CONSTRAINT fk_transactions_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE attachments (
	transaction_id UUID NOT NULL, 
	url TEXT NOT NULL, 
	name VARCHAR(240) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_attachments PRIMARY KEY (id), 
	CONSTRAINT fk_attachments_family_id_transaction_id_transactions FOREIGN KEY(family_id, transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_attachments_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_attachments_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE cash_settlements (
	original_transaction_id UUID NOT NULL, 
	settlement_transaction_id UUID NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	payment_date DATE NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_cash_settlements PRIMARY KEY (id), 
	CONSTRAINT fk_cash_settlements_family_id_original_transaction_id_t_4c1d FOREIGN KEY(family_id, original_transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_cash_settlements_family_id_settlement_transaction_id_46a8 FOREIGN KEY(family_id, settlement_transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_cash_settlements_positive CHECK (amount > 0), 
	CONSTRAINT uq_cash_settlements_family_id UNIQUE (family_id, id), 
	CONSTRAINT uq_cash_settlements_settlement_transaction_id UNIQUE (settlement_transaction_id), 
	CONSTRAINT fk_cash_settlements_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE invoice_items (
	invoice_id UUID NOT NULL, 
	transaction_id UUID NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_invoice_items PRIMARY KEY (id), 
	CONSTRAINT fk_invoice_items_family_id_invoice_id_invoices FOREIGN KEY(family_id, invoice_id) REFERENCES invoices (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_invoice_items_family_id_transaction_id_transactions FOREIGN KEY(family_id, transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_invoice_items_nonzero CHECK (amount <> 0), 
	CONSTRAINT uq_invoice_items_family_id_invoice_id_transaction_id UNIQUE (family_id, invoice_id, transaction_id), 
	CONSTRAINT uq_invoice_items_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_invoice_items_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE invoice_payments (
	invoice_id UUID NOT NULL, 
	transaction_id UUID NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	payment_date DATE NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_invoice_payments PRIMARY KEY (id), 
	CONSTRAINT fk_invoice_payments_family_id_invoice_id_invoices FOREIGN KEY(family_id, invoice_id) REFERENCES invoices (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_invoice_payments_family_id_transaction_id_transactions FOREIGN KEY(family_id, transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_invoice_payments_positive CHECK (amount > 0), 
	CONSTRAINT uq_invoice_payments_family_id UNIQUE (family_id, id), 
	CONSTRAINT uq_invoice_payments_transaction_id UNIQUE (transaction_id), 
	CONSTRAINT fk_invoice_payments_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE journals (
	transaction_id UUID NOT NULL, 
	event VARCHAR(30) NOT NULL, 
	economic_date DATE NOT NULL, 
	effective_date DATE NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_journals PRIMARY KEY (id), 
	CONSTRAINT fk_journals_family_id_transaction_id_transactions FOREIGN KEY(family_id, transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_journals_family_id_transaction_id_event UNIQUE (family_id, transaction_id, event), 
	CONSTRAINT uq_journals_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_journals_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE transaction_shares (
	transaction_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_transaction_shares PRIMARY KEY (id), 
	CONSTRAINT fk_transaction_shares_family_id_transaction_id_transactions FOREIGN KEY(family_id, transaction_id) REFERENCES transactions (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transaction_shares_family_id_user_id_family_members FOREIGN KEY(family_id, user_id) REFERENCES family_members (family_id, user_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_transaction_shares_positive CHECK (amount > 0), 
	CONSTRAINT uq_transaction_shares_family_id_transaction_id_user_id UNIQUE (family_id, transaction_id, user_id), 
	CONSTRAINT uq_transaction_shares_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_transaction_shares_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE TABLE journal_lines (
	journal_id UUID NOT NULL, 
	ledger_account_id UUID NOT NULL, 
	category_id UUID, 
	debit NUMERIC(12, 2) NOT NULL, 
	credit NUMERIC(12, 2) NOT NULL, 
	family_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_journal_lines PRIMARY KEY (id), 
	CONSTRAINT fk_journal_lines_family_id_journal_id_journals FOREIGN KEY(family_id, journal_id) REFERENCES journals (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_journal_lines_family_id_ledger_account_id_ledger_accounts FOREIGN KEY(family_id, ledger_account_id) REFERENCES ledger_accounts (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_journal_lines_family_id_category_id_categories FOREIGN KEY(family_id, category_id) REFERENCES categories (family_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_journal_lines_one_side CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)), 
	CONSTRAINT uq_journal_lines_family_id UNIQUE (family_id, id), 
	CONSTRAINT fk_journal_lines_family_id_families FOREIGN KEY(family_id) REFERENCES families (id) ON DELETE RESTRICT
);

CREATE INDEX ix_audit_family_time ON audit_logs (family_id, created_at);

CREATE INDEX ix_audit_logs_family_id ON audit_logs (family_id);

CREATE INDEX ix_categories_family_id ON categories (family_id);

CREATE INDEX ix_family_members_user_id ON family_members (user_id, family_id);

CREATE INDEX ix_idempotency_records_family_id ON idempotency_records (family_id);

CREATE INDEX ix_ledger_accounts_family_id ON ledger_accounts (family_id);

CREATE INDEX ix_accounts_family_id ON accounts (family_id);

CREATE INDEX ix_budgets_family_id ON budgets (family_id);

CREATE INDEX ix_wealth_positions_family_id ON wealth_positions (family_id);

CREATE INDEX ix_credit_cards_family_id ON credit_cards (family_id);

CREATE INDEX ix_goals_family_id ON goals (family_id);

CREATE INDEX ix_goal_contributions_family_id ON goal_contributions (family_id);

CREATE INDEX ix_invoices_family_due ON invoices (family_id, due_date);

CREATE INDEX ix_invoices_family_id ON invoices (family_id);

CREATE INDEX ix_recurrences_family_id ON recurrences (family_id);

CREATE INDEX ix_transactions_family_competency ON transactions (family_id, competency_date);

CREATE INDEX ix_transactions_family_id ON transactions (family_id);

CREATE INDEX ix_transactions_family_parent ON transactions (family_id, parent_id);

CREATE INDEX ix_transactions_family_related ON transactions (family_id, related_transaction_id);

CREATE INDEX ix_transactions_tags ON transactions USING gin (tags);

CREATE INDEX ix_attachments_family_id ON attachments (family_id);

CREATE INDEX ix_cash_settlements_family_id ON cash_settlements (family_id);

CREATE INDEX ix_invoice_items_family_id ON invoice_items (family_id);

CREATE INDEX ix_invoice_payments_family_id ON invoice_payments (family_id);

CREATE INDEX ix_journals_economic ON journals (family_id, economic_date);

CREATE INDEX ix_journals_effective ON journals (family_id, effective_date);

CREATE INDEX ix_journals_family_id ON journals (family_id);

CREATE INDEX ix_transaction_shares_family_id ON transaction_shares (family_id);

CREATE INDEX ix_journal_lines_family_id ON journal_lines (family_id);

CREATE INDEX ix_lines_balance ON journal_lines (family_id, ledger_account_id);

CREATE INDEX ix_lines_category ON journal_lines (family_id, category_id);

CREATE FUNCTION audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE before_value jsonb; after_value jsonb; row_value jsonb; monetary_key text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN before_value := to_jsonb(OLD); END IF;
          IF TG_OP <> 'DELETE' THEN after_value := to_jsonb(NEW); END IF;
          -- Strings decimais evitam float na desserialização de JSONB no cliente.
          FOREACH monetary_key IN ARRAY ARRAY['amount','credit_limit','debit','credit','target_amount','monthly_contribution'] LOOP
            IF before_value ? monetary_key THEN
              before_value:=jsonb_set(before_value,ARRAY[monetary_key],to_jsonb(before_value->>monetary_key));
            END IF;
            IF after_value ? monetary_key THEN
              after_value:=jsonb_set(after_value,ARRAY[monetary_key],to_jsonb(after_value->>monetary_key));
            END IF;
          END LOOP;
          row_value := COALESCE(after_value, before_value);
          INSERT INTO audit_logs(id, family_id, actor_id, table_name, row_key, action, old_value, new_value, created_at)
          VALUES(gen_random_uuid(), (row_value->>'family_id')::uuid,
                 NULLIF(current_setting('app.actor_id', true), '')::uuid,
                 TG_TABLE_NAME, COALESCE(row_value->>'id', row_value->>'user_id'),
                 TG_OP, before_value, after_value, now());
          RETURN COALESCE(NEW, OLD);
        END $$;

CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Registro imutável: %', TG_TABLE_NAME USING ERRCODE='23514';
        END $$;

CREATE FUNCTION validate_journal() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; fam uuid; total numeric; count_lines integer;
        BEGIN
          IF TG_TABLE_NAME='journals' THEN target:=NEW.id; ELSE target:=NEW.journal_id; END IF;
          fam:=NEW.family_id;
          SELECT COALESCE(sum(debit-credit),0), count(*) INTO total,count_lines
            FROM journal_lines WHERE family_id=fam AND journal_id=target;
          IF count_lines < 2 OR total <> 0 THEN
            RAISE EXCEPTION 'Diário sem contrapartida ou desbalanceado' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$;

CREATE FUNCTION line_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT 1 FROM journals j WHERE j.family_id=NEW.family_id
            AND j.id=NEW.journal_id
            AND j.xmin::text = (pg_current_xact_id()::text::numeric % 4294967296)::text) THEN
            RAISE EXCEPTION 'Não se acrescentam linhas a diário já confirmado' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;

CREATE FUNCTION protect_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Transação não pode ser excluída' USING ERRCODE='23514';
          END IF;
          IF OLD.status <> 'planned' AND
            (to_jsonb(OLD)-'paid_date') IS DISTINCT FROM (to_jsonb(NEW)-'paid_date') THEN
            RAISE EXCEPTION 'Fato confirmado exige lançamento compensatório' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;

CREATE FUNCTION validate_tree() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; fam uuid; root_row transactions%ROWTYPE; part_sum numeric;
                split_sum numeric; shares_sum numeric; installments_count integer;
        BEGIN
          fam:=NEW.family_id;
          IF TG_TABLE_NAME='transaction_shares' THEN target:=NEW.transaction_id;
          ELSE target:=COALESCE(NEW.parent_id, NEW.id); END IF;
          SELECT * INTO root_row FROM transactions WHERE family_id=fam AND id=target;
          IF root_row.node <> 'root' THEN
            RAISE EXCEPTION 'Pai deve ser transação raiz' USING ERRCODE='23514';
          END IF;
          SELECT sum(amount) FILTER(WHERE node='installment'),
                 sum(amount) FILTER(WHERE node='split'),
                 count(*) FILTER(WHERE node='installment')
            INTO part_sum,split_sum,installments_count FROM transactions
            WHERE family_id=fam AND parent_id=target;
          IF (part_sum IS NOT NULL AND part_sum <> root_row.amount)
             OR (split_sum IS NOT NULL AND split_sum <> root_row.amount) THEN
            RAISE EXCEPTION 'Soma das parcelas/rateios difere da compra' USING ERRCODE='23514';
          END IF;
          IF root_row.kind='expense' AND root_row.card_id IS NOT NULL AND root_row.status='posted'
             AND (part_sum IS NULL OR installments_count<>root_row.installment_count) THEN
            RAISE EXCEPTION 'Compra no cartão exige todas as parcelas' USING ERRCODE='23514';
          END IF;
          SELECT sum(amount) INTO shares_sum FROM transaction_shares WHERE family_id=fam AND transaction_id=target;
          IF shares_sum IS NOT NULL AND shares_sum <> root_row.amount THEN
            RAISE EXCEPTION 'Soma das participações difere da compra' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$;

CREATE FUNCTION validate_invoice_item() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE tx transactions%ROWTYPE; inv invoices%ROWTYPE;
        BEGIN
          SELECT * INTO tx FROM transactions WHERE family_id=NEW.family_id AND id=NEW.transaction_id;
          SELECT * INTO inv FROM invoices WHERE family_id=NEW.family_id AND id=NEW.invoice_id;
          IF tx.card_id IS DISTINCT FROM inv.card_id THEN
            RAISE EXCEPTION 'Item e fatura devem pertencer ao mesmo cartão' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;

CREATE CONSTRAINT TRIGGER journal_balanced AFTER INSERT ON journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_journal();

CREATE CONSTRAINT TRIGGER lines_balanced AFTER INSERT ON journal_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_journal();

CREATE TRIGGER lines_insert_guard BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION line_insert_guard();

CREATE TRIGGER protect_transaction BEFORE UPDATE OR DELETE ON transactions FOR EACH ROW EXECUTE FUNCTION protect_transaction();

CREATE CONSTRAINT TRIGGER transaction_tree AFTER INSERT OR UPDATE ON transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_tree();

CREATE CONSTRAINT TRIGGER shares_total AFTER INSERT ON transaction_shares DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_tree();

CREATE TRIGGER invoice_card_guard BEFORE INSERT ON invoice_items FOR EACH ROW EXECUTE FUNCTION validate_invoice_item();

CREATE TRIGGER immutable_journals BEFORE UPDATE OR DELETE ON journals FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_journal_lines BEFORE UPDATE OR DELETE ON journal_lines FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_invoice_items BEFORE UPDATE OR DELETE ON invoice_items FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_invoice_payments BEFORE UPDATE OR DELETE ON invoice_payments FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_cash_settlements BEFORE UPDATE OR DELETE ON cash_settlements FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_audit_logs BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_transaction_shares BEFORE UPDATE OR DELETE ON transaction_shares FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_attachments BEFORE UPDATE OR DELETE ON attachments FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_goal_contributions BEFORE UPDATE OR DELETE ON goal_contributions FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_idempotency_records BEFORE UPDATE OR DELETE ON idempotency_records FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER immutable_ledger_accounts BEFORE UPDATE OR DELETE ON ledger_accounts FOR EACH ROW EXECUTE FUNCTION reject_mutation();

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON audit_logs USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_categories AFTER INSERT OR UPDATE OR DELETE ON categories FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE categories ENABLE ROW LEVEL SECURITY;

ALTER TABLE categories FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON categories USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_family_members AFTER INSERT OR UPDATE OR DELETE ON family_members FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;

ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON idempotency_records USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_ledger_accounts AFTER INSERT OR UPDATE OR DELETE ON ledger_accounts FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE ledger_accounts ENABLE ROW LEVEL SECURITY;

ALTER TABLE ledger_accounts FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON ledger_accounts USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_accounts AFTER INSERT OR UPDATE OR DELETE ON accounts FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;

ALTER TABLE accounts FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON accounts USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_budgets AFTER INSERT OR UPDATE OR DELETE ON budgets FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE budgets ENABLE ROW LEVEL SECURITY;

ALTER TABLE budgets FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON budgets USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_wealth_positions AFTER INSERT OR UPDATE OR DELETE ON wealth_positions FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE wealth_positions ENABLE ROW LEVEL SECURITY;

ALTER TABLE wealth_positions FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON wealth_positions USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_credit_cards AFTER INSERT OR UPDATE OR DELETE ON credit_cards FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE credit_cards ENABLE ROW LEVEL SECURITY;

ALTER TABLE credit_cards FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON credit_cards USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_goals AFTER INSERT OR UPDATE OR DELETE ON goals FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;

ALTER TABLE goals FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON goals USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_goal_contributions AFTER INSERT OR UPDATE OR DELETE ON goal_contributions FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE goal_contributions ENABLE ROW LEVEL SECURITY;

ALTER TABLE goal_contributions FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON goal_contributions USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_invoices AFTER INSERT OR UPDATE OR DELETE ON invoices FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

ALTER TABLE invoices FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON invoices USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_recurrences AFTER INSERT OR UPDATE OR DELETE ON recurrences FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE recurrences ENABLE ROW LEVEL SECURITY;

ALTER TABLE recurrences FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON recurrences USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_transactions AFTER INSERT OR UPDATE OR DELETE ON transactions FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;

ALTER TABLE transactions FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON transactions USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_attachments AFTER INSERT OR UPDATE OR DELETE ON attachments FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE attachments ENABLE ROW LEVEL SECURITY;

ALTER TABLE attachments FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON attachments USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_cash_settlements AFTER INSERT OR UPDATE OR DELETE ON cash_settlements FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE cash_settlements ENABLE ROW LEVEL SECURITY;

ALTER TABLE cash_settlements FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON cash_settlements USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_invoice_items AFTER INSERT OR UPDATE OR DELETE ON invoice_items FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE invoice_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE invoice_items FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON invoice_items USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_invoice_payments AFTER INSERT OR UPDATE OR DELETE ON invoice_payments FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE invoice_payments ENABLE ROW LEVEL SECURITY;

ALTER TABLE invoice_payments FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON invoice_payments USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_journals AFTER INSERT OR UPDATE OR DELETE ON journals FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE journals ENABLE ROW LEVEL SECURITY;

ALTER TABLE journals FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON journals USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_transaction_shares AFTER INSERT OR UPDATE OR DELETE ON transaction_shares FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE transaction_shares ENABLE ROW LEVEL SECURITY;

ALTER TABLE transaction_shares FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON transaction_shares USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);

CREATE TRIGGER audit_journal_lines AFTER INSERT OR UPDATE OR DELETE ON journal_lines FOR EACH ROW EXECUTE FUNCTION audit_row();

ALTER TABLE journal_lines ENABLE ROW LEVEL SECURITY;

ALTER TABLE journal_lines FORCE ROW LEVEL SECURITY;

CREATE POLICY family_isolation ON journal_lines USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid);
COMMIT;
