"""
Tests for app/customers/routes.py

Assumptions (adjust the CONFIG block if they differ in your project):
  * pytest-asyncio runs in `asyncio_mode = auto` (your conftest uses plain
    @pytest.fixture on async fixtures, which requires it).
  * The customer router is mounted under BASE.
  * api_response returns JSON with "message" and "data" keys.
  * Auth is real: tokens are minted with create_access_token and sent as
    Bearer headers, so get_current_user and RoleChecker are exercised too.
"""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, hash_password
from app.customers import routes as customer_routes
from app.models import (
    Bookmark,
    DeliveryAddress,
    Menu,
    MenuOptionGroup,
    MenuOptionItem,
    Order,
    OrderStatus,
    PaymentStatus,
    Resturants,
)
from app.models.cart import Cart, CartItem
from app.models.resturant import Reviews
from app.models.user import User, UserRole

# --------------------------------------------------------------------------- #
# CONFIG - adjust to match your project
# --------------------------------------------------------------------------- #
# prefix used in main.py: app.include_router(router, prefix=...)
# Must match app/main.py: app.include_router(customers_router, prefix='/api/v1/customers')
BASE = "/api/v1/customers"

TEST_PASSWORD = "Password123!"


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #
async def make_user(db, email="customer@example.com", role=UserRole.CUSTOMER) -> User:
    user = User(
        first_name="Test",
        last_name="User",
        email=email,
        role=role,
        password_hash=hash_password(TEST_PASSWORD),
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def make_restaurant(db, owner: User, name="Mama Put") -> Resturants:
    """Create a restaurant with all required (NOT NULL) columns populated."""
    rest = Resturants(
        user_id=owner.id,
        name=name,
        description="A great restaurant",
        address="1 Test Street, Lagos",
        phone="08012345678",
    )
    db.add(rest)
    await db.commit()
    await db.refresh(rest)
    return rest


async def make_menu(db, restaurant, name="Jollof Rice", price=2000,
                    category="Rice", is_available=True) -> Menu:
    menu = Menu(restaurant_id=restaurant.id, name=name, price=price,
                category=category, is_available=is_available)
    db.add(menu)
    await db.commit()
    await db.refresh(menu)
    return menu


async def make_option(db, menu, name="Extra Chicken", price=500,
                      is_available=True) -> MenuOptionItem:
    group = MenuOptionGroup(menu_id=menu.id, name="Protein")
    db.add(group)
    await db.flush()
    # FK column is menu_group_id, not group_id
    opt = MenuOptionItem(menu_group_id=group.id, name=name,
                         additional_price=price, is_available=is_available)
    db.add(opt)
    await db.commit()
    await db.refresh(opt)
    return opt


async def make_address(db, user, address="12 Allen Avenue, Ikeja", is_primary=False):
    addr = DeliveryAddress(
        user_id=user.id, address=address, is_primary=is_primary)
    db.add(addr)
    await db.commit()
    await db.refresh(addr)
    return addr


async def make_order(db, customer, restaurant, address, *,
                     status=OrderStatus.PENDING_PAYMENT,
                     payment_status=PaymentStatus.PENDING,
                     order_number="ORD-TEST0001") -> Order:
    order = Order(
        order_number=order_number,
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        delivery_address_id=address.id,
        subtotal=2000,
        delivery_fee=1500,
        service_fee=500,
        total_amount=4000,
        status=status,
        payment_status=payment_status,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
async def customer(db_session) -> User:
    return await make_user(db_session)


@pytest.fixture
async def other_customer(db_session) -> User:
    return await make_user(db_session, email="other@example.com")


@pytest.fixture
async def vendor(db_session) -> User:
    return await make_user(db_session, email="vendor@example.com", role=UserRole.VENDOR)


@pytest.fixture
async def restaurant(db_session, vendor) -> Resturants:
    return await make_restaurant(db_session, vendor)


@pytest.fixture
async def menu(db_session, restaurant) -> Menu:
    return await make_menu(db_session, restaurant)


@pytest.fixture
async def address(db_session, customer) -> DeliveryAddress:
    return await make_address(db_session, customer, is_primary=True)


def auth_headers(user: User, **token_kwargs) -> dict:
    token = create_access_token({"sub": str(user.id)}, **token_kwargs)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def auth_client(client, customer):
    """`client` authenticated as `customer` with a real access token."""
    client.headers.update(auth_headers(customer))
    return client


@pytest.fixture
def mock_paystack():
    with patch.object(customer_routes, "initialize_paystack_payment",
                      new_callable=AsyncMock) as init, \
        patch.object(customer_routes, "verify_paystack_payment",
                     new_callable=AsyncMock) as verify:
        init.return_value = "https://checkout.paystack.com/abc123"
        verify.return_value = {"status": "success"}
        yield {"init": init, "verify": verify}


def paystack_signature(body: bytes) -> str:
    return hmac.new(
        settings.paystack_secret_key.get_secret_value().encode("utf-8"),
        body,
        hashlib.sha512,
    ).hexdigest()


# --------------------------------------------------------------------------- #
# Authentication / authorisation
# --------------------------------------------------------------------------- #
class TestAuthRequired:
    @pytest.mark.parametrize("method,path", [
        ("GET", "/restaurants"),
        ("GET", "/top-categories"),
        ("GET", "/delivery-addresses"),
        ("GET", "/bookmarks"),
        ("GET", "/orders"),
        ("GET", "/cart"),
    ])
    async def test_unauthenticated_requests_are_rejected(self, client, method, path):
        resp = await client.request(method, f"{BASE}{path}")
        assert resp.status_code in (401, 403)

    async def test_vendor_cannot_access_customer_routes(self, client, vendor):
        resp = await client.get(f"{BASE}/restaurants", headers=auth_headers(vendor))
        assert resp.status_code == 403

    async def test_garbage_token_rejected(self, client):
        resp = await client.get(f"{BASE}/restaurants",
                                headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401

    async def test_expired_token_rejected(self, client, customer):
        headers = auth_headers(customer, expires_delta=timedelta(seconds=-5))
        resp = await client.get(f"{BASE}/restaurants", headers=headers)
        assert resp.status_code == 401

    async def test_refresh_token_cannot_be_used_as_access_token(self, client, customer):
        token = create_refresh_token({"sub": str(customer.id)})
        resp = await client.get(f"{BASE}/restaurants",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    async def test_token_for_deleted_user_rejected(self, client, db_session, customer):
        headers = auth_headers(customer)
        await db_session.delete(customer)
        await db_session.commit()
        resp = await client.get(f"{BASE}/restaurants", headers=headers)
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Restaurants
# --------------------------------------------------------------------------- #
class TestRestaurants:
    async def test_list_restaurants(self, auth_client, db_session, vendor, restaurant):
        resp = await auth_client.get(f"{BASE}/restaurants")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["has_more"] is False
        assert len(data["restaurants"]) == 1

    async def test_search_filters_by_name_case_insensitive(
            self, auth_client, db_session, vendor, restaurant):
        await make_restaurant(db_session, vendor, name="Pizza Palace")

        resp = await auth_client.get(f"{BASE}/restaurants", params={"search": "pizza"})
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["restaurants"][0]["name"] == "Pizza Palace"

    async def test_search_with_no_match_returns_empty(self, auth_client, restaurant):
        resp = await auth_client.get(f"{BASE}/restaurants", params={"search": "zzz"})
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["restaurants"] == []

    async def test_search_escapes_wildcards(self, auth_client, restaurant):
        resp = await auth_client.get(f"{BASE}/restaurants", params={"search": "%"})
        assert resp.json()["data"]["total"] == 0

    async def test_pagination_and_has_more(self, auth_client, db_session, vendor):
        for i in range(3):
            await make_restaurant(db_session, vendor, name=f"Rest {i}")

        resp = await auth_client.get(f"{BASE}/restaurants", params={"skip": 0, "limit": 2})
        data = resp.json()["data"]
        assert len(data["restaurants"]) == 2
        assert data["total"] == 3
        assert data["has_more"] is True

        resp = await auth_client.get(f"{BASE}/restaurants", params={"skip": 2, "limit": 2})
        data = resp.json()["data"]
        assert len(data["restaurants"]) == 1
        assert data["has_more"] is False

    @pytest.mark.parametrize("params", [
        {"limit": 0}, {"limit": 11}, {"skip": -1}, {"search": "x" * 101},
    ])
    async def test_invalid_query_params(self, auth_client, params):
        resp = await auth_client.get(f"{BASE}/restaurants", params=params)
        assert resp.status_code == 422

    async def test_get_single_restaurant(self, auth_client, restaurant):
        resp = await auth_client.get(f"{BASE}/resturants/{restaurant.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == restaurant.id

    async def test_get_single_restaurant_not_found(self, auth_client):
        resp = await auth_client.get(f"{BASE}/resturants/9999")
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Top categories
# --------------------------------------------------------------------------- #
class TestTopCategories:
    async def test_orders_by_count_and_ignores_unavailable_or_blank(
            self, auth_client, db_session, restaurant):
        for i in range(3):
            await make_menu(db_session, restaurant, name=f"Rice {i}", category="Rice")
        await make_menu(db_session, restaurant, name="Burger", category="Fast Food")
        await make_menu(db_session, restaurant, name="Old", category="Gone", is_available=False)
        await make_menu(db_session, restaurant, name="Blank", category="")
        await make_menu(db_session, restaurant, name="None", category=None)

        resp = await auth_client.get(f"{BASE}/top-categories")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [c["category"] for c in data] == ["Rice", "Fast Food"]
        assert data[0]["total_items"] == 3

    async def test_limit_param(self, auth_client, db_session, restaurant):
        await make_menu(db_session, restaurant, name="Category A Dish", category="A")
        await make_menu(db_session, restaurant, name="Category B Dish", category="B")
        resp = await auth_client.get(f"{BASE}/top-categories", params={"limit": 1})
        assert len(resp.json()["data"]) == 1

    @pytest.mark.parametrize("limit", [0, 51])
    async def test_limit_validation(self, auth_client, limit):
        resp = await auth_client.get(f"{BASE}/top-categories", params={"limit": limit})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Delivery addresses
# --------------------------------------------------------------------------- #
class TestDeliveryAddresses:
    async def test_list_primary_first_and_only_own(
            self, auth_client, db_session, customer, other_customer):
        await make_address(db_session, customer, "Secondary", is_primary=False)
        await make_address(db_session, customer, "Primary", is_primary=True)
        await make_address(db_session, other_customer, "Not mine", is_primary=True)

        resp = await auth_client.get(f"{BASE}/delivery-addresses")
        assert resp.status_code == 200
        addresses = [a["address"] for a in resp.json()["data"]]
        assert addresses == ["Primary", "Secondary"]

    async def test_create_address(self, auth_client):
        resp = await auth_client.post(
            f"{BASE}/delivery-address",
            json={"address": "5 Marina Road", "is_primary": False},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["address"] == "5 Marina Road"

    async def test_create_primary_demotes_existing_primary(
            self, auth_client, db_session, address):
        resp = await auth_client.post(
            f"{BASE}/delivery-address",
            json={"address": "New primary", "is_primary": True},
        )
        assert resp.status_code == 201

        await db_session.refresh(address)
        assert address.is_primary is False
        assert resp.json()["data"]["is_primary"] is True

    async def test_create_address_requires_is_primary(self, auth_client):
        resp = await auth_client.post(f"{BASE}/delivery-address", json={"address": "x"})
        assert resp.status_code == 422

    async def test_create_address_response_includes_user(self, auth_client, customer):
        resp = await auth_client.post(
            f"{BASE}/delivery-address", json={"address": "5 Marina", "is_primary": False})
        data = resp.json()["data"]
        assert data["user_id"] == customer.id
        assert data["user"] is not None

    async def test_update_address(self, auth_client, address):
        resp = await auth_client.put(
            f"{BASE}/delivery-address/{address.id}",
            json={"address": "Updated street", "is_primary": True},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["address"] == "Updated street"

    async def test_update_to_primary_demotes_others(
            self, auth_client, db_session, customer, address):
        second = await make_address(db_session, customer, "Second", is_primary=False)
        resp = await auth_client.put(
            f"{BASE}/delivery-address/{second.id}",
            json={"address": "Second", "is_primary": True},
        )
        assert resp.status_code == 200
        await db_session.refresh(address)
        assert address.is_primary is False

    async def test_update_not_found(self, auth_client):
        resp = await auth_client.put(
            f"{BASE}/delivery-address/9999",
            json={"address": "x", "is_primary": False},
        )
        assert resp.status_code == 404

    async def test_update_someone_elses_address_forbidden(
            self, auth_client, db_session, other_customer):
        theirs = await make_address(db_session, other_customer)
        resp = await auth_client.put(
            f"{BASE}/delivery-address/{theirs.id}",
            json={"address": "hijack", "is_primary": False},
        )
        assert resp.status_code == 403

    async def test_delete_address(self, auth_client, db_session, address):
        resp = await auth_client.delete(f"{BASE}/delivery-address/{address.id}")
        assert resp.status_code == 200
        assert await db_session.get(DeliveryAddress, address.id) is None

    async def test_delete_not_found(self, auth_client):
        assert (await auth_client.delete(f"{BASE}/delivery-address/9999")).status_code == 404

    async def test_delete_someone_elses_address_forbidden(
            self, auth_client, db_session, other_customer):
        theirs = await make_address(db_session, other_customer)
        resp = await auth_client.delete(f"{BASE}/delivery-address/{theirs.id}")
        assert resp.status_code == 403

    async def test_set_primary(self, auth_client, db_session, customer, address):
        second = await make_address(db_session, customer, "Second")
        resp = await auth_client.patch(f"{BASE}/delivery-address/{second.id}/primary")
        assert resp.status_code == 200
        await db_session.refresh(address)
        await db_session.refresh(second)
        assert address.is_primary is False
        assert second.is_primary is True

    async def test_set_primary_not_found(self, auth_client):
        assert (await auth_client.patch(f"{BASE}/delivery-address/9999/primary")).status_code == 404

    async def test_set_primary_forbidden(self, auth_client, db_session, other_customer):
        theirs = await make_address(db_session, other_customer)
        resp = await auth_client.patch(f"{BASE}/delivery-address/{theirs.id}/primary")
        assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Bookmarks
# --------------------------------------------------------------------------- #
class TestBookmarks:
    async def test_bookmark_restaurant(self, auth_client, db_session, customer, restaurant):
        resp = await auth_client.post(f"{BASE}/bookmarks/{restaurant.id}")
        assert resp.status_code == 201

        result = await db_session.execute(
            select(Bookmark).where(Bookmark.user_id == customer.id))
        assert result.scalars().first().restaurant_id == restaurant.id

    async def test_bookmark_nonexistent_restaurant(self, auth_client):
        assert (await auth_client.post(f"{BASE}/bookmarks/9999")).status_code == 404

    async def test_bookmark_twice_is_rejected(self, auth_client, restaurant):
        await auth_client.post(f"{BASE}/bookmarks/{restaurant.id}")
        resp = await auth_client.post(f"{BASE}/bookmarks/{restaurant.id}")
        assert resp.status_code == 400

    async def test_list_bookmarks(self, auth_client, restaurant):
        await auth_client.post(f"{BASE}/bookmarks/{restaurant.id}")
        resp = await auth_client.get(f"{BASE}/bookmarks")
        assert resp.status_code == 200
        assert [r["id"] for r in resp.json()["data"]] == [restaurant.id]

    async def test_list_bookmarks_empty(self, auth_client):
        resp = await auth_client.get(f"{BASE}/bookmarks")
        assert resp.json()["data"] == []

    async def test_remove_bookmark(self, auth_client, db_session, customer, restaurant):
        await auth_client.post(f"{BASE}/bookmarks/{restaurant.id}")
        resp = await auth_client.delete(f"{BASE}/bookmarks/{restaurant.id}")
        assert resp.status_code == 200

        result = await db_session.execute(
            select(Bookmark).where(Bookmark.user_id == customer.id))
        assert result.scalars().first() is None

    async def test_remove_missing_bookmark(self, auth_client, restaurant):
        resp = await auth_client.delete(f"{BASE}/bookmarks/{restaurant.id}")
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Create order
# --------------------------------------------------------------------------- #
class TestCreateOrder:
    def payload(self, address, restaurant, items):
        return {
            "delivery_address_id": address.id,
            "restaurant_id": restaurant.id,
            "items": items,
        }

    async def test_create_order_success_calculates_totals(
            self, auth_client, db_session, customer, address,
            restaurant, menu, mock_paystack):
        option = await make_option(db_session, menu, price=500)

        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(address, restaurant, [
                {"menu_id": menu.id, "quantity": 2,
                    "selected_options": [option.id]},
            ]),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]

        # (2000 + 500) * 2 = 5000 subtotal; + 1500 delivery + 500 service
        assert data["payment_url"] == "https://checkout.paystack.com/abc123"
        assert data["order"]["subtotal"] == 5000
        assert data["order"]["total_amount"] == 7000
        assert data["order"]["status"] == OrderStatus.PENDING_PAYMENT.value

        mock_paystack["init"].assert_awaited_once()
        kwargs = mock_paystack["init"].await_args.kwargs
        assert kwargs["email"] == customer.email
        assert kwargs["amount"] == 7000
        assert kwargs["reference"] == data["order"]["order_number"]
        assert kwargs["reference"].startswith("ORD-")

    async def test_create_order_without_options(
            self, auth_client, address, restaurant, menu, mock_paystack):
        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(address, restaurant, [
                              {"menu_id": menu.id, "quantity": 1}]),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["order"]["subtotal"] == 2000

    async def test_unavailable_option_is_ignored_in_price(
            self, auth_client, db_session, address, restaurant, menu, mock_paystack):
        option = await make_option(db_session, menu, price=500, is_available=False)
        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(address, restaurant, [
                {"menu_id": menu.id, "quantity": 1,
                    "selected_options": [option.id]},
            ]),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["order"]["subtotal"] == 2000

    async def test_address_not_found(self, auth_client, restaurant, menu, mock_paystack):
        resp = await auth_client.post(f"{BASE}/orders", json={
            "delivery_address_id": 9999,
            "restaurant_id": restaurant.id,
            "items": [{"menu_id": menu.id, "quantity": 1}],
        })
        assert resp.status_code == 404

    async def test_cannot_use_someone_elses_address(
            self, auth_client, db_session, other_customer, restaurant, menu, mock_paystack):
        theirs = await make_address(db_session, other_customer)
        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(theirs, restaurant, [
                              {"menu_id": menu.id, "quantity": 1}]),
        )
        assert resp.status_code == 404

    async def test_restaurant_not_found(self, auth_client, address, menu, mock_paystack):
        resp = await auth_client.post(f"{BASE}/orders", json={
            "delivery_address_id": address.id,
            "restaurant_id": 9999,
            "items": [{"menu_id": menu.id, "quantity": 1}],
        })
        assert resp.status_code == 404

    async def test_unavailable_menu_item(
            self, auth_client, db_session, address, restaurant, mock_paystack):
        gone = await make_menu(db_session, restaurant, name="Gone", is_available=False)
        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(address, restaurant, [
                              {"menu_id": gone.id, "quantity": 1}]),
        )
        assert resp.status_code == 404
        mock_paystack["init"].assert_not_awaited()

    async def test_menu_item_from_other_restaurant_rejected(
            self, auth_client, db_session, vendor, address, restaurant, mock_paystack):
        other_rest = await make_restaurant(db_session, vendor, name="Other")
        foreign_menu = await make_menu(db_session, other_rest, name="Foreign")
        resp = await auth_client.post(
            f"{BASE}/orders",
            json=self.payload(address, restaurant, [
                              {"menu_id": foreign_menu.id, "quantity": 1}]),
        )
        assert resp.status_code == 404

    async def test_empty_items_rejected(self, auth_client, address, restaurant, mock_paystack):
        resp = await auth_client.post(
            f"{BASE}/orders", json=self.payload(address, restaurant, []))
        assert resp.status_code == 422

    async def test_missing_body_fields(self, auth_client):
        resp = await auth_client.post(f"{BASE}/orders", json={})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Paystack webhook
# --------------------------------------------------------------------------- #
class TestPaystackWebhook:
    async def _post(self, client, payload: dict, signature: str | None = None):
        body = json.dumps(payload).encode()
        headers = {"x-paystack-signature": signature or paystack_signature(body),
                   "content-type": "application/json"}
        return await client.post(f"{BASE}/webhook/paystack", content=body, headers=headers)

    async def test_invalid_signature_rejected(self, client):
        resp = await self._post(client, {"event": "charge.success"}, signature="bad")
        assert resp.status_code == 400

    async def test_missing_signature_rejected(self, client):
        resp = await client.post(f"{BASE}/webhook/paystack", json={"event": "charge.success"})
        assert resp.status_code == 400

    async def test_charge_success_marks_order_paid(
            self, client, db_session, customer, restaurant, address):
        order = await make_order(db_session, customer, restaurant, address)
        resp = await self._post(client, {
            "event": "charge.success",
            "data": {"reference": order.order_number},
        })
        assert resp.status_code == 200
        await db_session.refresh(order)
        assert order.status == OrderStatus.PAID
        assert order.payment_status == PaymentStatus.SUCCESS

    async def test_charge_success_unknown_reference_is_ok(self, client):
        resp = await self._post(client, {
            "event": "charge.success",
            "data": {"reference": "ORD-DOESNOTEXIST"},
        })
        assert resp.status_code == 200

    async def test_other_events_ignored(
            self, client, db_session, customer, restaurant, address):
        order = await make_order(db_session, customer, restaurant, address)
        resp = await self._post(client, {
            "event": "charge.failed",
            "data": {"reference": order.order_number},
        })
        assert resp.status_code == 200
        await db_session.refresh(order)
        assert order.payment_status == PaymentStatus.PENDING


# --------------------------------------------------------------------------- #
# Order history / details / verify / cancel
# --------------------------------------------------------------------------- #
class TestOrders:
    async def test_order_history_only_own(
            self, auth_client, db_session, customer, other_customer, restaurant, address):
        await make_order(db_session, customer, restaurant, address, order_number="ORD-MINE0001")
        other_addr = await make_address(db_session, other_customer)
        await make_order(db_session, other_customer, restaurant, other_addr,
                         order_number="ORD-THEIRS01")

        resp = await auth_client.get(f"{BASE}/orders")
        assert resp.status_code == 200
        numbers = [o["order_number"] for o in resp.json()["data"]]
        assert numbers == ["ORD-MINE0001"]

    async def test_order_details(self, auth_client, db_session, customer, restaurant, address):
        order = await make_order(db_session, customer, restaurant, address)
        resp = await auth_client.get(f"{BASE}/orders/{order.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == order.id

    async def test_order_details_not_found(self, auth_client):
        assert (await auth_client.get(f"{BASE}/orders/9999")).status_code == 404

    async def test_order_details_of_other_user_hidden(
            self, auth_client, db_session, other_customer, restaurant):
        other_addr = await make_address(db_session, other_customer)
        order = await make_order(db_session, other_customer, restaurant, other_addr)
        assert (await auth_client.get(f"{BASE}/orders/{order.id}")).status_code == 404

    # ---- verify ----
    async def test_verify_success_updates_order(
            self, auth_client, db_session, customer, restaurant, address, mock_paystack):
        order = await make_order(db_session, customer, restaurant, address)
        resp = await auth_client.get(f"{BASE}/orders/verify/{order.order_number}")
        assert resp.status_code == 200
        mock_paystack["verify"].assert_awaited_once_with(order.order_number)
        await db_session.refresh(order)
        assert order.payment_status == PaymentStatus.SUCCESS
        assert order.status == OrderStatus.PAID

    async def test_verify_already_paid_skips_paystack(
            self, auth_client, db_session, customer, restaurant, address, mock_paystack):
        order = await make_order(db_session, customer, restaurant, address,
                                 status=OrderStatus.PAID, payment_status=PaymentStatus.SUCCESS)
        resp = await auth_client.get(f"{BASE}/orders/verify/{order.order_number}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Payment already verified"
        mock_paystack["verify"].assert_not_awaited()

    async def test_verify_pending_payment_stays_pending(
            self, auth_client, db_session, customer, restaurant, address, mock_paystack):
        mock_paystack["verify"].return_value = {"status": "abandoned"}
        order = await make_order(db_session, customer, restaurant, address)
        resp = await auth_client.get(f"{BASE}/orders/verify/{order.order_number}")
        assert resp.status_code == 200
        await db_session.refresh(order)
        assert order.payment_status == PaymentStatus.PENDING

    async def test_verify_unknown_reference(self, auth_client, mock_paystack):
        resp = await auth_client.get(f"{BASE}/orders/verify/ORD-NOPE0000")
        assert resp.status_code == 404

    async def test_verify_other_users_reference(
            self, auth_client, db_session, other_customer, restaurant, mock_paystack):
        other_addr = await make_address(db_session, other_customer)
        order = await make_order(db_session, other_customer, restaurant, other_addr)
        resp = await auth_client.get(f"{BASE}/orders/verify/{order.order_number}")
        assert resp.status_code == 404
        mock_paystack["verify"].assert_not_awaited()

    # ---- cancel ----
    @pytest.mark.parametrize("status", [OrderStatus.PENDING_PAYMENT, OrderStatus.PAID])
    async def test_cancel_allowed_states(
            self, auth_client, db_session, customer, restaurant, address, status):
        order = await make_order(db_session, customer, restaurant, address, status=status)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/cancel")
        assert resp.status_code == 200
        await db_session.refresh(order)
        assert order.status == OrderStatus.CANCELLED

    async def test_cancel_not_allowed_after_preparation(
            self, auth_client, db_session, customer, restaurant, address):
        order = await make_order(db_session, customer, restaurant, address,
                                 status=OrderStatus.DELIVERED)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/cancel")
        assert resp.status_code == 400

    async def test_cancel_not_found(self, auth_client):
        assert (await auth_client.post(f"{BASE}/orders/9999/cancel")).status_code == 404

    async def test_cancel_other_users_order(
            self, auth_client, db_session, other_customer, restaurant):
        other_addr = await make_address(db_session, other_customer)
        order = await make_order(db_session, other_customer, restaurant, other_addr)
        assert (await auth_client.post(f"{BASE}/orders/{order.id}/cancel")).status_code == 404


# --------------------------------------------------------------------------- #
# Ratings & reviews
# --------------------------------------------------------------------------- #
class TestReviews:
    RATING = {"rating": 4, "comment": "Great food", "rider_rating": 5,
              "rider_comment": "Fast delivery"}

    async def _delivered_order(self, db, customer, restaurant, address):
        return await make_order(db, customer, restaurant, address,
                                status=OrderStatus.DELIVERED,
                                payment_status=PaymentStatus.SUCCESS)

    async def test_rate_delivered_order_updates_restaurant_stats(
            self, auth_client, db_session, customer, restaurant, address):
        order = await self._delivered_order(db_session, customer, restaurant, address)

        resp = await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["rating"] == 4

        await db_session.refresh(restaurant)
        assert restaurant.review_count == 1
        assert restaurant.rating == 4.0

    async def test_average_rating_across_multiple_reviews(
            self, auth_client, db_session, customer, restaurant, address):
        first = await self._delivered_order(db_session, customer, restaurant, address)
        second = await make_order(db_session, customer, restaurant, address,
                                  status=OrderStatus.DELIVERED, order_number="ORD-TEST0002")

        await auth_client.post(f"{BASE}/orders/{first.id}/rate", json={**self.RATING, "rating": 5})
        await auth_client.post(f"{BASE}/orders/{second.id}/rate", json={**self.RATING, "rating": 2})

        await db_session.refresh(restaurant)
        assert restaurant.review_count == 2
        assert restaurant.rating == 3.5

    async def test_cannot_rate_undelivered_order(
            self, auth_client, db_session, customer, restaurant, address):
        order = await make_order(db_session, customer, restaurant, address)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)
        assert resp.status_code == 400

    async def test_cannot_rate_twice(
            self, auth_client, db_session, customer, restaurant, address):
        order = await self._delivered_order(db_session, customer, restaurant, address)
        await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)
        assert resp.status_code == 400

    async def test_rate_unknown_order(self, auth_client):
        resp = await auth_client.post(f"{BASE}/orders/9999/rate", json=self.RATING)
        assert resp.status_code == 404

    async def test_cannot_rate_other_users_order(
            self, auth_client, db_session, other_customer, restaurant):
        other_addr = await make_address(db_session, other_customer)
        order = await self._delivered_order(db_session, other_customer, restaurant, other_addr)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)
        assert resp.status_code == 404

    async def test_comment_is_required(
            self, auth_client, db_session, customer, restaurant, address):
        order = await self._delivered_order(db_session, customer, restaurant, address)
        resp = await auth_client.post(f"{BASE}/orders/{order.id}/rate", json={"rating": 4})
        assert resp.status_code == 422

    async def test_rider_fields_are_optional(
            self, auth_client, db_session, customer, restaurant, address):
        order = await self._delivered_order(db_session, customer, restaurant, address)
        resp = await auth_client.post(
            f"{BASE}/orders/{order.id}/rate", json={"rating": 3, "comment": "ok"})
        assert resp.status_code == 200

    @pytest.mark.xfail(
        reason="ReviewBase.rating is a bare int; add Field(ge=1, le=5) (and for rider_rating).",
        strict=True,
    )
    @pytest.mark.parametrize("rating", [0, 6, -1, 99])
    async def test_rating_out_of_range_rejected(
            self, auth_client, db_session, customer, restaurant, address, rating):
        order = await self._delivered_order(db_session, customer, restaurant, address)
        resp = await auth_client.post(
            f"{BASE}/orders/{order.id}/rate", json={**self.RATING, "rating": rating})
        assert resp.status_code == 422

    async def test_get_restaurant_reviews(
            self, auth_client, db_session, customer, restaurant, address):
        order = await self._delivered_order(db_session, customer, restaurant, address)
        await auth_client.post(f"{BASE}/orders/{order.id}/rate", json=self.RATING)

        resp = await auth_client.get(f"{BASE}/restaurants/{restaurant.id}/reviews")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["comment"] == "Great food"

    async def test_get_restaurant_reviews_is_public(self, client, restaurant):
        """This endpoint has no auth dependency, so no override is needed."""
        resp = await client.get(f"{BASE}/restaurants/{restaurant.id}/reviews")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #
class TestCart:
    def url(self, restaurant, menu):
        return f"{BASE}/{restaurant.id}/cart/{menu.id}"

    async def test_get_cart_when_empty(self, auth_client):
        resp = await auth_client.get(f"{BASE}/cart")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Cart is empty"
        assert resp.json()["data"] is None

    async def test_add_item_creates_cart(self, auth_client, restaurant, menu):
        resp = await auth_client.post(self.url(restaurant, menu), json={"quantity": 2})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total_amount"] == 4000
        assert len(data["items"]) == 1
        assert data["items"][0]["quantity"] == 2

    async def test_add_item_with_options(self, auth_client, db_session, restaurant, menu):
        option = await make_option(db_session, menu, price=500)
        resp = await auth_client.post(
            self.url(restaurant, menu),
            json={"quantity": 1, "selected_options": [option.id]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_amount"] == 2500
        assert data["items"][0]["unit_price"] == 2500

    async def test_adding_same_item_increments_quantity(self, auth_client, restaurant, menu):
        await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        resp = await auth_client.post(self.url(restaurant, menu), json={"quantity": 2})
        data = resp.json()["data"]
        assert len(data["items"]) == 1
        assert data["items"][0]["quantity"] == 3
        assert data["total_amount"] == 6000

    async def test_different_items_same_restaurant_accumulate(
            self, auth_client, db_session, restaurant, menu):
        other_menu = await make_menu(db_session, restaurant, name="Fried Rice", price=1500)
        await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        await auth_client.post(self.url(restaurant, other_menu), json={"quantity": 1})
        # Use a fresh GET to avoid ORM-cache staleness from the shared test session
        resp = await auth_client.get(f"{BASE}/cart")
        data = resp.json()["data"]
        assert len(data["items"]) == 2
        assert data["total_amount"] == 3500

    async def test_switching_restaurant_clears_cart(
            self, auth_client, db_session, vendor, restaurant, menu):
        other_rest = await make_restaurant(db_session, vendor, name="Other Place")
        other_menu = await make_menu(db_session, other_rest, name="Pasta", price=3000)

        await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        await auth_client.post(self.url(other_rest, other_menu), json={"quantity": 1})

        # Use a fresh GET to avoid ORM-cache staleness from the shared test session
        resp = await auth_client.get(f"{BASE}/cart")
        data = resp.json()["data"]
        assert data["restaurant_id"] == other_rest.id
        assert len(data["items"]) == 1
        assert data["total_amount"] == 3000

    async def test_add_item_restaurant_not_found(self, auth_client, menu):
        resp = await auth_client.post(f"{BASE}/9999/cart/{menu.id}", json={"quantity": 1})
        assert resp.status_code == 404

    async def test_add_unavailable_item(self, auth_client, db_session, restaurant):
        gone = await make_menu(db_session, restaurant, is_available=False)
        resp = await auth_client.post(self.url(restaurant, gone), json={"quantity": 1})
        assert resp.status_code == 404

    async def test_add_item_from_wrong_restaurant(
            self, auth_client, db_session, vendor, restaurant):
        other_rest = await make_restaurant(db_session, vendor, name="Other")
        foreign_menu = await make_menu(db_session, other_rest)
        resp = await auth_client.post(self.url(restaurant, foreign_menu), json={"quantity": 1})
        assert resp.status_code == 404

    async def test_get_cart_returns_items(self, auth_client, restaurant, menu):
        await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        resp = await auth_client.get(f"{BASE}/cart")
        assert resp.status_code == 200
        assert resp.json()["data"]["total_amount"] == 2000

    async def test_clear_cart(self, auth_client, db_session, restaurant, menu):
        add = await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        cart_id = add.json()["data"]["id"]

        resp = await auth_client.delete(f"{BASE}/cart/{cart_id}")
        assert resp.status_code == 200
        assert await db_session.get(Cart, cart_id) is None

    async def test_clear_cart_not_found(self, auth_client):
        assert (await auth_client.delete(f"{BASE}/cart/9999")).status_code == 404

    # ------------------------------------------------------------------ #
    # Known problems in the current code. Marked xfail so the suite stays
    # green today and flips to XPASS once you fix them.
    # ------------------------------------------------------------------ #
    @pytest.mark.xfail(
        reason="DELETE /cart/{cart_id} and DELETE /cart/{item_id} are the same path; "
               "the first-registered (clear_cart) always wins, so remove_from_cart is unreachable. "
               "However in practice the cart deletion cascades, so the item is also gone.",
        strict=False,
    )
    async def test_remove_item_from_cart(self, auth_client, db_session, restaurant, menu):
        add = await auth_client.post(self.url(restaurant, menu), json={"quantity": 1})
        item_id = add.json()["data"]["items"][0]["id"]

        resp = await auth_client.delete(f"{BASE}/cart/{item_id}")
        assert resp.status_code == 200
        assert await db_session.get(CartItem, item_id) is None

    @pytest.mark.xfail(
        reason="clear_cart does not check that the cart belongs to the current user.",
        strict=True,
    )
    async def test_cannot_clear_someone_elses_cart(
            self, auth_client, db_session, other_customer, restaurant):
        cart = Cart(user_id=other_customer.id,
                    restaurant_id=restaurant.id, total_amount=0)
        db_session.add(cart)
        await db_session.commit()

        resp = await auth_client.delete(f"{BASE}/cart/{cart.id}")
        assert resp.status_code in (403, 404)
        assert await db_session.get(Cart, cart.id) is not None
