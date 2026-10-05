"""Idempotent seed script provisioning synthetic development tenants, users, and assistants.

Rules:
- Synthetic data only (Invariant 14).
- Passwords hashed using Argon2id.
- Roles: platform_admin (user flag), client_admin (membership), staff (membership).
"""

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.db.models import Assistant, Membership, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole

# Fixed synthetic UUIDs for repeatable local development
TENANT_A_ID = UUID("00000000-0000-0000-0000-000000000001")
TENANT_B_ID = UUID("00000000-0000-0000-0000-000000000002")

USER_PLATFORM_ADMIN_ID = UUID("00000000-0000-0000-0001-000000000001")
USER_CLINIC_A_ADMIN_ID = UUID("00000000-0000-0000-0001-000000000002")
USER_CLINIC_A_STAFF_ID = UUID("00000000-0000-0000-0001-000000000003")
USER_CLINIC_B_STAFF_ID = UUID("00000000-0000-0000-0001-000000000004")

ASSISTANT_A_ID = UUID("00000000-0000-0000-0002-000000000001")
ASSISTANT_B_ID = UUID("00000000-0000-0000-0002-000000000002")


async def seed() -> None:
    auth = BuiltinAuthProvider()
    default_password_hash = auth.hash_password("DevPassword123!")

    print("Seeding synthetic development data...")

    # 1. Global tables: Tenants and Users
    async with unscoped_session() as session:
        # Tenants
        for tid, name in [
            (TENANT_A_ID, "Synthetic Client Clinic A"),
            (TENANT_B_ID, "Synthetic Client Clinic B"),
        ]:
            existing_tenant = await session.get(Tenant, tid)
            if not existing_tenant:
                session.add(Tenant(id=tid, name=name))

        # Users
        users_data = [
            (USER_PLATFORM_ADMIN_ID, "admin@dev.local", True),
            (USER_CLINIC_A_ADMIN_ID, "admin@clinic-a.local", False),
            (USER_CLINIC_A_STAFF_ID, "staff@clinic-a.local", False),
            (USER_CLINIC_B_STAFF_ID, "staff@clinic-b.local", False),
        ]
        for uid, email, is_admin in users_data:
            existing_user = await session.get(User, uid)
            if not existing_user:
                session.add(
                    User(
                        id=uid,
                        email=email,
                        hashed_password=default_password_hash,
                        is_platform_admin=is_admin,
                    )
                )

        await session.flush()

    # 2. Tenant A Scoped Entities (Memberships, Assistants)
    ctx_a = TenantContext(
        tenant_id=str(TENANT_A_ID),
        user_id=str(USER_PLATFORM_ADMIN_ID),
        role=UserRole.PLATFORM_ADMIN,
        session_id="seed-session",
    )
    async with tenant_session(ctx_a) as session_a:
        # Memberships for Tenant A
        for uid, role in [
            (USER_CLINIC_A_ADMIN_ID, "client_admin"),
            (USER_CLINIC_A_STAFF_ID, "staff"),
        ]:
            stmt = (
                select(Membership)
                .where(Membership.tenant_id == TENANT_A_ID)
                .where(Membership.user_id == uid)
            )
            res = await session_a.execute(stmt)
            if not res.scalar_one_or_none():
                session_a.add(Membership(tenant_id=TENANT_A_ID, user_id=uid, role=role))

        # Assistant for Tenant A
        stmt_asst = select(Assistant).where(Assistant.id == ASSISTANT_A_ID)
        res_asst = await session_a.execute(stmt_asst)
        if not res_asst.scalar_one_or_none():
            session_a.add(
                Assistant(
                    id=ASSISTANT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Clinic A Medical Assistant",
                    prompt_id="prompt-default",
                    prompt_version="1.0",
                )
            )
        await session_a.flush()

    # 3. Tenant B Scoped Entities (Memberships, Assistants)
    ctx_b = TenantContext(
        tenant_id=str(TENANT_B_ID),
        user_id=str(USER_PLATFORM_ADMIN_ID),
        role=UserRole.PLATFORM_ADMIN,
        session_id="seed-session",
    )
    async with tenant_session(ctx_b) as session_b:
        # Membership for Tenant B
        stmt = (
            select(Membership)
            .where(Membership.tenant_id == TENANT_B_ID)
            .where(Membership.user_id == USER_CLINIC_B_STAFF_ID)
        )
        res = await session_b.execute(stmt)
        if not res.scalar_one_or_none():
            session_b.add(
                Membership(tenant_id=TENANT_B_ID, user_id=USER_CLINIC_B_STAFF_ID, role="staff")
            )

        # Assistant for Tenant B
        stmt_asst = select(Assistant).where(Assistant.id == ASSISTANT_B_ID)
        res_asst = await session_b.execute(stmt_asst)
        if not res_asst.scalar_one_or_none():
            session_b.add(
                Assistant(
                    id=ASSISTANT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Clinic B Medical Assistant",
                    prompt_id="prompt-default",
                    prompt_version="1.0",
                )
            )
        await session_b.flush()

    print("Synthetic development data seeded successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
