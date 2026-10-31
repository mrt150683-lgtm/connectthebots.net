-- Deliberately no account identifier, email, IP, token or content in receipts.
CREATE TABLE deletion_receipts (
    deleted_on TEXT NOT NULL CHECK(length(deleted_on) = 10)
);
