from app.models.user import User

user = User(
    email="test@example.com",
    hashed_password=None,
    google_sub="123",
    full_name="test",
    avatar_url="http",
    is_active=True,
)

print(f"is_active: {user.is_active}")
print(f"is_active is False: {user.is_active is False}")

# What if we don't pass is_active?
user2 = User(
    email="test2@example.com",
)
print(f"user2 is_active: {user2.is_active}")
print(f"user2 is_active is False: {user2.is_active is False}")

