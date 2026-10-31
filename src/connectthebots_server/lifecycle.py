"""One atomic account erasure path, shared by the person and the operator."""
from datetime import timezone

from .board import touch_board
from .recovery import advance_erasure


class AccountLifecycle:
    def __init__(self, database, clock, board, interactions, notifications, moderation):
        self.database, self.clock = database, clock
        self.board, self.interactions = board, interactions
        self.notifications, self.moderation = notifications, moderation

    def delete_account(self, user_id, *, connection=None):
        if connection is None:
            with self.database.transaction() as active:
                return self.delete_account(user_id, connection=active)
        if connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
            return False
        touch_board(connection, self.clock)
        self.moderation.erase_owner(user_id, connection=connection)
        self.notifications.erase_owner(user_id, connection=connection)
        self.interactions.erase_owner(user_id, connection=connection)
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))
        connection.execute("INSERT INTO deletion_receipts(deleted_on) VALUES(?)",
                           (self.clock().astimezone(timezone.utc).date().isoformat(),))
        advance_erasure(connection)
        return True
