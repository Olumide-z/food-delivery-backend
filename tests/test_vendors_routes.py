"""
Tests for the vendor routes.

Assumptions you may need to adjust (search for "ADJUST"):
  * the router module is `app.vendors.routes`
  * the router is mounted at BASE below
  * model constructors for User / Order / OrderItem (I can't see those files)
  * pytest-asyncio runs in "auto" mode (your conftest uses plain @pytest.fixture
    on async fixtures, so it must already be)
"""
import typing
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.core.security import CurrentUser
from app.models import Menu, MenuOptionGroup, MenuOptionItem, Order, OrderStatus, Resturants
from app.models.order import OrderItem
from app.models.user import User, UserRole
# ADJUST if the module lives elsewhere
from app.vendors import routes as vendor_routes

# ADJUST to the prefix used in main.py's include_router
BASE = "/api/v1/vendors"

# The dependency behind the `CurrentUser` alias (Annotated[User, Depends(fn)])
CURRENT_USER_DEP = next(
    m.dependency for m in typing.get_args(CurrentUser)[1:] if hasattr(m, "dependency")
)


# --------------------------------------------------------------------------- #
# Factories / fixtures
# --------------------------------------------------------------------------- #
async def make_user(db: AsyncSession, email: str, role=UserRole.VENDOR) -> User:
    # ADJUST: use whatever required columns your User model has
    name_part = email.split("@")[0]
    user = User(
        email=email,
        first_name=name_part,
        last_name="Test",
        password_hash="not-a-real-hash",
        role=role,
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def make_restaurant(db: AsyncSession, owner: User, name: str = "Mama Put") -> Resturants:
    restaurant = Resturants(
        name=name,
        description="Good food",
        address="12 Allen Avenue, Ikeja",
        phone="08012345678",
        email="r@example.com",
        website=None,
        user_id=owner.id,
    )
    db.add(restaurant)
    await db.commit()
    await db.refresh(restaurant)
    return restaurant


async def make_menu(db: AsyncSession, restaurant: Resturants, name: str = "Jollof Rice", price: int = 2500) -> Menu:
    menu = Menu(name=name, price=price,
                restaurant_id=restaurant.id, is_available=True)
    db.add(menu)
    await db.commit()
    await db.refresh(menu)
    return menu


async def make_group(db: AsyncSession, menu: Menu, name: str = "Protein") -> MenuOptionGroup:
    group = MenuOptionGroup(menu_id=menu.id, name=name,
                            is_required=False, min_selectable=0, max_selectable=2)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def make_option_item(db: AsyncSession, group: MenuOptionGroup, name: str = "Chicken") -> MenuOptionItem:
    item = MenuOptionItem(menu_group_id=group.id, name=name,
                          additional_price=500, is_available=True)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def make_order(
    db: AsyncSession,
    restaurant: Resturants,
    customer: User,
    status: OrderStatus = OrderStatus.PAID,
    total: float = 5000,
    menu: Menu | None = None,
    quantity: int = 1,
) -> Order:
    # Create a delivery address stub to satisfy the NOT NULL FK
    from app.models.customer import DeliveryAddress
    import uuid
    addr = DeliveryAddress(user_id=customer.id, address="1 Test Street")
    db.add(addr)
    await db.flush()

    total_int = int(total)
    order = Order(
        order_number=str(uuid.uuid4())[:32],
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        delivery_address_id=addr.id,
        status=status,
        subtotal=total_int,
        delivery_fee=0,
        service_fee=0,
        total_amount=total_int,
    )
    db.add(order)
    await db.flush()
    if menu is not None:
        db.add(OrderItem(
            order_id=order.id,
            menu_id=menu.id,
            quantity=quantity,
            unit_price=menu.price,
            total_price=menu.price * quantity,
        ))
    await db.commit()
    await db.refresh(order)
    return order


@pytest.fixture
async def vendor(db_session) -> User:
    return await make_user(db_session, "vendor1@example.com")


@pytest.fixture
async def other_vendor(db_session) -> User:
    return await make_user(db_session, "vendor2@example.com")


@pytest.fixture
async def customer(db_session) -> User:
    return await make_user(db_session, "customer@example.com", role=UserRole.CUSTOMER)


@pytest.fixture
def login_as():
    """Bypass real auth: make `require_vendor` and `CurrentUser` resolve to the given user."""
    overrides = main_module.app.dependency_overrides

    def _login(user: User):
        overrides[vendor_routes.require_vendor] = lambda: user
        overrides[CURRENT_USER_DEP] = lambda: user

    return _login


@pytest.fixture
async def restaurant(db_session, vendor) -> Resturants:
    return await make_restaurant(db_session, vendor)


@pytest.fixture
async def menu(db_session, restaurant) -> Menu:
    return await make_menu(db_session, restaurant)


@pytest.fixture
def mock_s3():
    with patch(
        "app.vendors.routes.upload_image_to_s3",
        new_callable=AsyncMock,
        return_value="https://cdn.example.com/img.png",
    ) as m:
        yield m


RESTAURANT_PAYLOAD = {
    "name": "Chop Life",
    "description": "Nigerian cuisine",
    "address": "5 Awolowo Road, Ikoyi",
    "phone": "08098765432",
    "email": "chop@example.com",
    "website": "https://chop.example.com",
}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", ""),
            ("GET", "/me"),
            ("POST", ""),
            ("PATCH", "/1"),
            ("DELETE", "/1"),
            ("GET", "/1/analytics"),
            ("PATCH", "/orders/1/status"),
        ],
    )
    async def test_unauthenticated_requests_rejected(self, client: AsyncClient, method, path):
        response = await client.request(method, f"{BASE}{path}")
        assert response.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Restaurants
# --------------------------------------------------------------------------- #
class TestListRestaurants:
    async def test_returns_paginated_list(self, client, db_session, vendor, login_as):
        login_as(vendor)
        for i in range(3):
            await make_restaurant(db_session, vendor, name=f"Place {i}")

        response = await client.get(BASE, params={"skip": 0, "limit": 2})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 3
        assert len(data["restaurants"]) == 2
        assert data["has_more"] is True

    async def test_last_page_has_no_more(self, client, db_session, vendor, login_as):
        login_as(vendor)
        for i in range(3):
            await make_restaurant(db_session, vendor, name=f"Place {i}")

        response = await client.get(BASE, params={"skip": 2, "limit": 10})

        data = response.json()["data"]
        assert len(data["restaurants"]) == 1
        assert data["has_more"] is False

    async def test_results_ordered_by_name(self, client, db_session, vendor, login_as):
        login_as(vendor)
        for name in ("Charlie", "Alpha", "Bravo"):
            await make_restaurant(db_session, vendor, name=name)

        response = await client.get(BASE)

        names = [r["name"] for r in response.json()["data"]["restaurants"]]
        assert names == ["Alpha", "Bravo", "Charlie"]

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 11}, {"skip": -1}])
    async def test_invalid_pagination_params(self, client, vendor, login_as, params):
        login_as(vendor)
        response = await client.get(BASE, params=params)
        assert response.status_code == 422


class TestMyRestaurants:
    async def test_only_returns_own_restaurants(self, client, db_session, vendor, other_vendor, login_as):
        await make_restaurant(db_session, vendor, name="Mine")
        await make_restaurant(db_session, other_vendor, name="Theirs")
        login_as(vendor)

        response = await client.get(f"{BASE}/me")

        assert response.status_code == 200
        data = response.json()["data"]
        assert [r["name"] for r in data] == ["Mine"]

    async def test_empty_when_vendor_has_none(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/me")
        assert response.status_code == 200
        assert response.json()["data"] == []


class TestGetRestaurant:
    async def test_success(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/{restaurant.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == restaurant.id
        assert data["user_id"] == vendor.id

    async def test_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/9999")
        assert response.status_code == 404

    async def test_other_users_can_view(self, client, restaurant, customer, login_as):
        login_as(customer)
        response = await client.get(f"{BASE}/{restaurant.id}")
        assert response.status_code == 200


class TestCreateRestaurant:
    async def test_success(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.post(BASE, json=RESTAURANT_PAYLOAD)

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == RESTAURANT_PAYLOAD["name"]
        assert data["user_id"] == vendor.id

    async def test_persisted_in_db(self, client, db_session, vendor, login_as):
        from sqlalchemy import select

        login_as(vendor)
        response = await client.post(BASE, json=RESTAURANT_PAYLOAD)
        new_id = response.json()["data"]["id"]

        row = (await db_session.execute(select(Resturants).where(Resturants.id == new_id))).scalars().first()
        assert row is not None
        assert row.user_id == vendor.id

    @pytest.mark.parametrize(
        "override",
        [
            {"name": "ab"},                 # too short
            {"name": "x" * 51},             # too long
            {"phone": "123"},               # too short
            {"description": ""},            # empty
            {"address": "ab"},              # too short
        ],
    )
    async def test_validation_errors(self, client, vendor, login_as, override):
        login_as(vendor)
        response = await client.post(BASE, json={**RESTAURANT_PAYLOAD, **override})
        assert response.status_code == 422

    async def test_missing_required_field(self, client, vendor, login_as):
        login_as(vendor)
        payload = {k: v for k, v in RESTAURANT_PAYLOAD.items() if k != "name"}
        response = await client.post(BASE, json=payload)
        assert response.status_code == 422


class TestUpdateRestaurant:
    async def test_partial_update(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.patch(f"{BASE}/{restaurant.id}", json={"name": "New Name"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "New Name"
        assert data["address"] == restaurant.address  # untouched

    async def test_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.patch(f"{BASE}/9999", json={"name": "Whatever"})
        assert response.status_code == 404

    async def test_forbidden_for_non_owner(self, client, restaurant, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.patch(f"{BASE}/{restaurant.id}", json={"name": "Hijacked"})
        assert response.status_code == 403

    async def test_validation_error(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.patch(f"{BASE}/{restaurant.id}", json={"name": "ab"})
        assert response.status_code == 422


class TestDeleteRestaurant:
    async def test_success(self, client, db_session, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.delete(f"{BASE}/{restaurant.id}")
        assert response.status_code == 200

        follow_up = await client.get(f"{BASE}/{restaurant.id}")
        assert follow_up.status_code == 404

    async def test_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.delete(f"{BASE}/9999")
        assert response.status_code == 404

    async def test_forbidden_for_non_owner(self, client, restaurant, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.delete(f"{BASE}/{restaurant.id}")
        assert response.status_code == 403


class TestRestaurantImageUploads:
    @pytest.mark.parametrize(
        "endpoint,field,folder",
        [
            ("upload-logo", "logo", "restaurants/logos"),
            ("upload-cover", "cover_image", "restaurants/covers"),
        ],
    )
    async def test_upload_success(self, client, vendor, restaurant, login_as, mock_s3, endpoint, field, folder):
        login_as(vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/{endpoint}",
            files={"file": ("pic.png", b"\x89PNG fake", "image/png")},
        )

        assert response.status_code == 200
        assert response.json()[
            "data"][field] == "https://cdn.example.com/img.png"
        mock_s3.assert_awaited_once()
        assert mock_s3.await_args.kwargs["folder"] == folder

    @pytest.mark.parametrize("endpoint", ["upload-logo", "upload-cover"])
    async def test_upload_forbidden_for_non_owner(
        self, client, restaurant, other_vendor, login_as, mock_s3, endpoint
    ):
        login_as(other_vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/{endpoint}",
            files={"file": ("pic.png", b"data", "image/png")},
        )
        assert response.status_code in (403, 404)
        mock_s3.assert_not_awaited()

    async def test_upload_without_file(self, client, vendor, restaurant, login_as, mock_s3):
        login_as(vendor)
        response = await client.post(f"{BASE}/{restaurant.id}/upload-logo")
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Menus
# --------------------------------------------------------------------------- #
class TestCreateMenu:
    async def test_simple_menu(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/menus",
            json={"name": "Fried Rice", "price": 3000, "category": "Rice"},
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name"] == "Fried Rice"
        assert data["restaurant_id"] == restaurant.id
        assert data["menus_options"] == []

    async def test_menu_with_nested_option_groups(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        payload = {
            "name": "Burger",
            "price": 4000,
            "option_groups": [
                {
                    "name": "Extras",
                    "is_required": False,
                    "min_selectable": 0,
                    "max_selectable": 2,
                    "options": [
                        {"name": "Cheese", "additional_price": 300},
                        {"name": "Bacon", "additional_price": 500,
                            "is_available": False},
                    ],
                }
            ],
        }
        response = await client.post(f"{BASE}/{restaurant.id}/menus", json=payload)

        assert response.status_code == 201
        groups = response.json()["data"]["menus_options"]
        assert len(groups) == 1
        assert groups[0]["name"] == "Extras"
        assert {o["name"] for o in groups[0]["options"]} == {"Cheese", "Bacon"}
        bacon = next(o for o in groups[0]["options"] if o["name"] == "Bacon")
        assert bacon["is_available"] is False

    async def test_restaurant_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.post(f"{BASE}/9999/menus", json={"name": "Fried Rice", "price": 100})
        assert response.status_code == 404

    async def test_forbidden_for_non_owner(self, client, restaurant, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.post(f"{BASE}/{restaurant.id}/menus", json={"name": "Fried Rice", "price": 100})
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "override",
        [{"name": "ab"}, {"price": -1}, {"prep_time_minutes": -5},
            {"description": "x" * 501}],
    )
    async def test_validation_errors(self, client, vendor, restaurant, login_as, override):
        login_as(vendor)
        payload = {"name": "Fried Rice", "price": 100, **override}
        response = await client.post(f"{BASE}/{restaurant.id}/menus", json=payload)
        assert response.status_code == 422


class TestGetMenus:
    async def test_list_with_pagination(self, client, db_session, vendor, restaurant, login_as):
        login_as(vendor)
        for name in ("Beans", "Amala", "Ofada"):
            await make_menu(db_session, restaurant, name=name)

        response = await client.get(f"{BASE}/{restaurant.id}/menus", params={"limit": 2})

        data = response.json()["data"]
        assert response.status_code == 200
        assert data["total"] == 3
        assert [m["name"] for m in data["menus"]] == ["Amala", "Beans"]
        assert data["has_more"] is True

    async def test_only_includes_menus_of_that_restaurant(self, client, db_session, vendor, restaurant, login_as):
        login_as(vendor)
        other = await make_restaurant(db_session, vendor, name="Other Place")
        await make_menu(db_session, restaurant, name="Mine")
        await make_menu(db_session, other, name="NotMine")

        response = await client.get(f"{BASE}/{restaurant.id}/menus")

        names = [m["name"] for m in response.json()["data"]["menus"]]
        assert names == ["Mine"]

    async def test_get_single_menu(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/{restaurant.id}/menus/{menu.id}")
        assert response.status_code == 200
        assert response.json()["data"]["id"] == menu.id

    async def test_get_single_menu_wrong_restaurant(self, client, db_session, vendor, menu, login_as):
        login_as(vendor)
        other = await make_restaurant(db_session, vendor, name="Other Place")
        response = await client.get(f"{BASE}/{other.id}/menus/{menu.id}")
        assert response.status_code == 404

    async def test_get_single_menu_not_found(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/{restaurant.id}/menus/9999")
        assert response.status_code == 404


class TestUpdateMenu:
    async def test_partial_update(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.patch(
            f"{BASE}/{restaurant.id}/menus/{menu.id}",
            json={"price": 9999, "is_spicy": True},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["price"] == 9999
        assert data["is_spicy"] is True
        assert data["name"] == menu.name

    async def test_restaurant_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.patch(f"{BASE}/9999/menus/1", json={"price": 1})
        assert response.status_code == 404

    async def test_menu_not_found(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.patch(f"{BASE}/{restaurant.id}/menus/9999", json={"price": 1})
        assert response.status_code == 404

    async def test_forbidden_for_non_owner(self, client, restaurant, menu, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.patch(f"{BASE}/{restaurant.id}/menus/{menu.id}", json={"price": 1})
        assert response.status_code == 403

    async def test_menu_belonging_to_another_restaurant(self, client, db_session, vendor, restaurant, login_as):
        login_as(vendor)
        other = await make_restaurant(db_session, vendor, name="Other Place")
        other_menu = await make_menu(db_session, other, name="Elsewhere")

        response = await client.patch(f"{BASE}/{restaurant.id}/menus/{other_menu.id}", json={"price": 1})
        assert response.status_code == 403


class TestDeleteMenu:
    async def test_success(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.delete(f"{BASE}/{restaurant.id}/menus/{menu.id}")
        assert response.status_code == 200

        follow_up = await client.get(f"{BASE}/{restaurant.id}/menus/{menu.id}")
        assert follow_up.status_code == 404

    async def test_not_found(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.delete(f"{BASE}/{restaurant.id}/menus/9999")
        assert response.status_code == 404

    async def test_menu_belongs_to_other_restaurant(self, client, db_session, vendor, restaurant, login_as):
        login_as(vendor)
        other = await make_restaurant(db_session, vendor, name="Other Place")
        other_menu = await make_menu(db_session, other, name="Elsewhere")

        response = await client.delete(f"{BASE}/{restaurant.id}/menus/{other_menu.id}")
        assert response.status_code == 403

    async def test_does_not_check_restaurant_ownership(self, client, restaurant, menu, other_vendor, login_as):
        """
        Characterisation test for a likely bug: delete_menu only checks that the menu
        belongs to the restaurant in the URL, not that the *current user owns that
        restaurant*. Any vendor can delete any menu. When you fix this (e.g. by using
        verify_menu_ownership) flip the assertion to 403/404.
        """
        login_as(other_vendor)
        response = await client.delete(f"{BASE}/{restaurant.id}/menus/{menu.id}")
        assert response.status_code == 200  # currently succeeds; should be 403


class TestMenuImageUpload:
    async def test_success(self, client, vendor, restaurant, menu, login_as, mock_s3):
        login_as(vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/menus/{menu.id}/upload-image",
            files={"file": ("dish.jpg", b"jpgdata", "image/jpeg")},
        )

        assert response.status_code == 200
        assert response.json()[
            "data"]["image_url"] == "https://cdn.example.com/img.png"
        assert mock_s3.await_args.kwargs["folder"] == "menus"

    async def test_forbidden_for_non_owner(self, client, restaurant, menu, other_vendor, login_as, mock_s3):
        login_as(other_vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/menus/{menu.id}/upload-image",
            files={"file": ("dish.jpg", b"jpgdata", "image/jpeg")},
        )
        assert response.status_code in (403, 404)
        mock_s3.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Option groups
# --------------------------------------------------------------------------- #
class TestOptionGroups:
    def url(self, restaurant, menu, group=None):
        base = f"{BASE}/{restaurant.id}/menus/{menu.id}/groups"
        return f"{base}/{group.id}" if group else base

    async def test_create_with_options(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        payload = {
            "name": "Size",
            "is_required": True,
            "min_selectable": 1,
            "max_selectable": 1,
            "options": [{"name": "Small"}, {"name": "Large", "additional_price": 700}],
        }
        response = await client.post(self.url(restaurant, menu), json=payload)

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["menu_id"] == menu.id
        assert data["is_required"] is True
        assert len(data["options"]) == 2

    async def test_create_forbidden_for_non_owner(self, client, restaurant, menu, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.post(self.url(restaurant, menu), json={"name": "Size"})
        assert response.status_code in (403, 404)

    async def test_create_validation_error(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.post(self.url(restaurant, menu), json={"name": "Size", "max_selectable": 0})
        assert response.status_code == 422

    async def test_update(self, client, db_session, vendor, restaurant, menu, login_as):
        login_as(vendor)
        group = await make_group(db_session, menu)
        response = await client.patch(
            self.url(restaurant, menu, group),
            json={"name": "Toppings", "is_required": True},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["name"] == "Toppings"
        assert data["is_required"] is True

    async def test_update_group_not_found(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.patch(f"{self.url(restaurant, menu)}/9999", json={"name": "X1"})
        assert response.status_code == 404

    async def test_update_group_from_other_menu(self, client, db_session, vendor, restaurant, menu, login_as):
        login_as(vendor)
        other_menu = await make_menu(db_session, restaurant, name="Other Dish")
        foreign_group = await make_group(db_session, other_menu)

        response = await client.patch(self.url(restaurant, menu, foreign_group), json={"name": "Hack"})
        assert response.status_code == 404

    async def test_delete(self, client, db_session, vendor, restaurant, menu, login_as):
        login_as(vendor)
        group = await make_group(db_session, menu)

        response = await client.delete(self.url(restaurant, menu, group))
        assert response.status_code == 200

        again = await client.delete(self.url(restaurant, menu, group))
        assert again.status_code == 404

    async def test_delete_forbidden_for_non_owner(self, client, db_session, restaurant, menu, other_vendor, login_as):
        group = await make_group(db_session, menu)
        login_as(other_vendor)
        response = await client.delete(self.url(restaurant, menu, group))
        assert response.status_code in (403, 404)


# --------------------------------------------------------------------------- #
# Option items
# --------------------------------------------------------------------------- #
class TestOptionItems:
    def url(self, restaurant, menu, group, item=None):
        base = f"{BASE}/{restaurant.id}/menus/{menu.id}/groups/{group.id}/items"
        return f"{base}/{item.id}" if item else base

    @pytest.fixture
    async def group(self, db_session, menu):
        return await make_group(db_session, menu)

    async def test_create(self, client, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        response = await client.post(
            self.url(restaurant, menu, group),
            json={"name": "Extra Meat", "additional_price": 800},
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["menu_group_id"] == group.id
        assert data["additional_price"] == 800
        assert data["is_available"] is True

    async def test_create_group_not_found(self, client, vendor, restaurant, menu, login_as):
        login_as(vendor)
        response = await client.post(
            f"{BASE}/{restaurant.id}/menus/{menu.id}/groups/9999/items", json={"name": "Extra"}
        )
        assert response.status_code == 404

    async def test_create_negative_price_rejected(self, client, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        response = await client.post(
            self.url(restaurant, menu, group), json={"name": "Extra", "additional_price": -1}
        )
        assert response.status_code == 422

    async def test_create_forbidden_for_non_owner(self, client, restaurant, menu, group, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.post(self.url(restaurant, menu, group), json={"name": "Extra"})
        assert response.status_code in (403, 404)

    async def test_update(self, client, db_session, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        item = await make_option_item(db_session, group)

        response = await client.patch(
            self.url(restaurant, menu, group, item),
            json={"additional_price": 1000, "is_available": False},
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["additional_price"] == 1000
        assert data["is_available"] is False
        assert data["name"] == item.name

    async def test_update_item_not_found(self, client, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        response = await client.patch(f"{self.url(restaurant, menu, group)}/9999", json={"name": "Nope"})
        assert response.status_code == 404

    async def test_update_item_from_other_group(self, client, db_session, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        other_group = await make_group(db_session, menu, name="Other Group")
        foreign_item = await make_option_item(db_session, other_group)

        response = await client.patch(self.url(restaurant, menu, group, foreign_item), json={"name": "Hack"})
        assert response.status_code == 404

    async def test_delete(self, client, db_session, vendor, restaurant, menu, group, login_as):
        login_as(vendor)
        item = await make_option_item(db_session, group)

        response = await client.delete(self.url(restaurant, menu, group, item))
        assert response.status_code == 200

        again = await client.delete(self.url(restaurant, menu, group, item))
        assert again.status_code == 404

    async def test_delete_forbidden_for_non_owner(
        self, client, db_session, restaurant, menu, group, other_vendor, login_as
    ):
        item = await make_option_item(db_session, group)
        login_as(other_vendor)
        response = await client.delete(self.url(restaurant, menu, group, item))
        assert response.status_code in (403, 404)


# --------------------------------------------------------------------------- #
# Order status
# --------------------------------------------------------------------------- #
class TestVendorUpdateOrderStatus:
    url = f"{BASE}/orders/{{order_id}}/status"

    async def test_paid_to_preparing(self, client, db_session, vendor, restaurant, customer, login_as):
        login_as(vendor)
        order = await make_order(db_session, restaurant, customer, status=OrderStatus.PAID)

        response = await client.patch(self.url.format(order_id=order.id), json={"status": OrderStatus.PREPARING.value})

        assert response.status_code == 200
        assert response.json()["data"]["status"] == OrderStatus.PREPARING.value

    async def test_preparing_to_ready_for_pickup(self, client, db_session, vendor, restaurant, customer, login_as):
        login_as(vendor)
        order = await make_order(db_session, restaurant, customer, status=OrderStatus.PREPARING)

        response = await client.patch(
            self.url.format(order_id=order.id), json={"status": OrderStatus.READY_FOR_PICKUP.value}
        )

        assert response.status_code == 200
        assert response.json()[
            "data"]["status"] == OrderStatus.READY_FOR_PICKUP.value

    @pytest.mark.parametrize(
        "current,target",
        [
            (OrderStatus.PENDING_PAYMENT, OrderStatus.PREPARING),
            (OrderStatus.PREPARING, OrderStatus.PREPARING),
            (OrderStatus.PAID, OrderStatus.READY_FOR_PICKUP),
            (OrderStatus.CANCELLED, OrderStatus.READY_FOR_PICKUP),
        ],
    )
    async def test_invalid_transitions(
        self, client, db_session, vendor, restaurant, customer, login_as, current, target
    ):
        login_as(vendor)
        order = await make_order(db_session, restaurant, customer, status=current)

        response = await client.patch(self.url.format(order_id=order.id), json={"status": target.value})

        assert response.status_code == 400

    async def test_order_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.patch(self.url.format(order_id=9999), json={"status": OrderStatus.PREPARING.value})
        assert response.status_code == 404

    async def test_cannot_update_other_vendors_order(
        self, client, db_session, restaurant, other_vendor, customer, login_as
    ):
        order = await make_order(db_session, restaurant, customer, status=OrderStatus.PAID)
        login_as(other_vendor)

        response = await client.patch(self.url.format(order_id=order.id), json={"status": OrderStatus.PREPARING.value})

        assert response.status_code == 404

    async def test_invalid_status_value(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.patch(self.url.format(order_id=1), json={"status": "NOT_A_STATUS"})
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
class TestVendorAnalytics:
    async def test_empty_restaurant(self, client, vendor, restaurant, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/{restaurant.id}/analytics")

        assert response.status_code == 200
        assert response.json()["data"] == {
            "total_orders": 0,
            "total_revenue": 0.0,
            "pending_orders": 0,
            "active_orders": 0,
            "completed_orders": 0,
            "cancelled_orders": 0,
            "top_selling_items": [],
        }

    async def test_counts_by_status(self, client, db_session, vendor, restaurant, customer, login_as):
        login_as(vendor)
        statuses = [
            OrderStatus.PENDING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.PREPARING,
            OrderStatus.READY_FOR_PICKUP,
            OrderStatus.DELIVERED,
            OrderStatus.DELIVERED,
            OrderStatus.CANCELLED,
        ]
        for s in statuses:
            await make_order(db_session, restaurant, customer, status=s, total=1000)

        response = await client.get(f"{BASE}/{restaurant.id}/analytics")

        data = response.json()["data"]
        assert data["total_orders"] == 7
        assert data["pending_orders"] == 1
        assert data["active_orders"] == 3
        assert data["completed_orders"] == 2
        assert data["cancelled_orders"] == 1
        assert data["total_revenue"] == pytest.approx(7000.0)

    async def test_top_selling_items_ordered_and_limited(
        self, client, db_session, vendor, restaurant, customer, login_as
    ):
        login_as(vendor)
        quantities = {"A-dish": 1, "B-dish": 7, "C-dish": 3,
                      "D-dish": 9, "E-dish": 2, "F-dish": 5}
        for name, qty in quantities.items():
            m = await make_menu(db_session, restaurant, name=name)
            await make_order(db_session, restaurant, customer, menu=m, quantity=qty)

        response = await client.get(f"{BASE}/{restaurant.id}/analytics")

        top = response.json()["data"]["top_selling_items"]
        assert top == ["D-dish", "B-dish", "F-dish", "C-dish", "E-dish"]

    async def test_ignores_other_restaurants_orders(
        self, client, db_session, vendor, restaurant, customer, login_as
    ):
        login_as(vendor)
        other = await make_restaurant(db_session, vendor, name="Other Place")
        await make_order(db_session, restaurant, customer, total=1000)
        await make_order(db_session, other, customer, total=9000)

        response = await client.get(f"{BASE}/{restaurant.id}/analytics")

        data = response.json()["data"]
        assert data["total_orders"] == 1
        assert data["total_revenue"] == pytest.approx(1000.0)

    async def test_forbidden_for_non_owner(self, client, restaurant, other_vendor, login_as):
        login_as(other_vendor)
        response = await client.get(f"{BASE}/{restaurant.id}/analytics")
        assert response.status_code in (403, 404)

    async def test_restaurant_not_found(self, client, vendor, login_as):
        login_as(vendor)
        response = await client.get(f"{BASE}/9999/analytics")
        assert response.status_code in (403, 404)
