-- Execute com app.family_id configurado para uma família autorizada.
-- Resultado vazio significa que razão e auxiliar dos cartões concordam.
WITH card_balances AS (
    SELECT c.family_id, c.id, c.name,
           COALESCE(SUM(l.credit-l.debit), 0) AS book_liability
    FROM credit_cards c
    LEFT JOIN journal_lines l ON l.family_id=c.family_id AND l.ledger_account_id=c.ledger_account_id
    GROUP BY c.family_id,c.id,c.name
), charges AS (
    SELECT i.family_id,i.card_id,SUM(x.amount) AS amount
    FROM invoices i JOIN invoice_items x ON x.family_id=i.family_id AND x.invoice_id=i.id
    GROUP BY i.family_id,i.card_id
), payments AS (
    SELECT i.family_id,i.card_id,SUM(p.amount) AS amount
    FROM invoices i JOIN invoice_payments p ON p.family_id=i.family_id AND p.invoice_id=i.id
    GROUP BY i.family_id,i.card_id
)
SELECT b.id,b.name,b.book_liability,
       COALESCE(c.amount,0)-COALESCE(p.amount,0) AS subledger_liability
FROM card_balances b
LEFT JOIN charges c ON c.family_id=b.family_id AND c.card_id=b.id
LEFT JOIN payments p ON p.family_id=b.family_id AND p.card_id=b.id
WHERE b.book_liability <> COALESCE(c.amount,0)-COALESCE(p.amount,0);
