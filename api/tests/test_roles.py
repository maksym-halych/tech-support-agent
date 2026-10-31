from typing import Any

from lessons_api.roles import find_user, list_users, set_role, to_clerk_user


def clerk_user(user_id: str = "user_1", role: str | None = None) -> dict[str, Any]:
    return {
        "id": user_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "primary_email_address_id": "idn_2",
        "email_addresses": [
            {"id": "idn_1", "email_address": "old@example.com"},
            {"id": "idn_2", "email_address": "ada@example.com"},
        ],
        "public_metadata": {"role": role} if role else {},
    }


class FakeClerk:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        self.requests.append((method, path, body))
        return self.responses.pop(0)


def test_user_reads_primary_email_name_and_role() -> None:
    user = to_clerk_user(clerk_user(role="teacher"))

    assert user.email == "ada@example.com"
    assert user.name == "Ada Lovelace"
    assert user.role == "teacher"


def test_user_without_teacher_role_is_a_student() -> None:
    assert to_clerk_user(clerk_user()).role == "student"
    assert to_clerk_user(clerk_user(role="admin")).role == "student"


def test_set_role_patches_public_metadata() -> None:
    clerk = FakeClerk(clerk_user(role="teacher"))

    user = set_role(clerk, "user_1", "teacher")

    assert clerk.requests == [
        ("PATCH", "/users/user_1/metadata", {"public_metadata": {"role": "teacher"}})
    ]
    assert user.role == "teacher"


def test_find_user_by_email() -> None:
    clerk = FakeClerk([clerk_user()], [])

    assert find_user(clerk, "ada+clerk_test@example.com").id == "user_1"
    assert find_user(clerk, "nobody@example.com") is None
    assert clerk.requests[0][1] == "/users?email_address=ada%2Bclerk_test%40example.com"


def test_list_users_newest_first() -> None:
    clerk = FakeClerk([clerk_user("user_2"), clerk_user("user_1")])

    assert [user.id for user in list_users(clerk)] == ["user_2", "user_1"]
    assert "order_by=-created_at" in clerk.requests[0][1]
