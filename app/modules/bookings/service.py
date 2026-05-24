# from __future__ import annotations

# from datetime import date, timedelta
# from decimal import Decimal
# from uuid import uuid4

# from sqlalchemy.ext.asyncio import AsyncSession

# from app.common.exceptions import ConflictError, NotFoundError, ValidationError
# from app.common.enums import UserRole
# from app.common.service import BaseService
# from app.modules.orders.repository import OrderRepository
# from app.modules.orders.models import Order, DeliveryStop
# from app.modules.invoices.service import InvoiceService
# from app.modules.organizations.enums import OrganizationStatus
# from app.modules.organizations.models import Organization
# from app.modules.orders.models import Package
# from app.modules.user.models import User

# DEFAULT_INVOICE_TERMS_DAYS = 30


# class BookingService(BaseService):
#     def __init__(self, session: AsyncSession, request=None) -> None:
#         super().__init__(session, request)
#         self._order_repo = OrderRepository(session)

#     def _new_code(self, prefix: str, length: int = 12) -> str:
#         return f"{prefix}-{uuid4().hex[:length].upper()}"

#     async def _generate_unique_booking_reference(self) -> str:
#         for _ in range(8):
#             code = self._new_code("BKG", 10)
#             if not await self._order_repo.exists(reference_number=code):
#                 return code
#         raise ConflictError("Unable to generate unique booking reference, please retry.")

#     async def _generate_unique_master_label(self) -> str:
#         for _ in range(8):
#             code = self._new_code("ML", 12)
#             if not await self._order_repo.exists(master_label_id=code):
#                 return code
#         raise ConflictError("Unable to generate unique master label, please retry.")

#     async def _generate_unique_stop_tracking(self) -> str:
#         for _ in range(8):
#             code = self._new_code("TRK", 12)
#             if await self._order_repo.get_stop_by_tracking(code) is None:
#                 return code
#         raise ConflictError("Unable to generate unique tracking id, please retry.")

#     async def create_booking_tree(
#         self,
#         *,
#         customer_id: str,
#         contact_name: str,
#         contact_email: str,
#         contact_phone: str,
#         pickup_address_id: str | None,
#         pickup_instructions: str | None,
#         service_tier: str,
#         special_instructions: str | None,
#         notes: str | None,
#         delivery_stops: list[dict],
#     ) -> Order:
#         if not delivery_stops:
#             raise ValidationError("At least one delivery stop is required")
#         organization_id = await self._assert_customer_booking_allowed(customer_id)

#         order = await self._order_repo.create(
#             {
#                 "reference_number": await self._generate_unique_order_reference(),
#                 "master_label_id": await self._generate_unique_master_label(),
#                 "customer_id": customer_id,
#                 "organization_id": organization_id,
#                 "contact_name": contact_name,
#                 "contact_email": contact_email,
#                 "contact_phone": contact_phone,
#                 "pickup_address_id": pickup_address_id,
#                 "pickup_instructions": pickup_instructions,
#                 "service_tier": service_tier,
#                 "special_instructions": special_instructions,
#                 "subtotal": 0,
#                 "vat_amount": 0,
#                 "total_amount": 0,
#                 "payment_status": "pending",
#                 "status": "draft",
#                 "notes": notes,
#             }
#         )

#         for stop_payload in delivery_stops:
#             stop = DeliveryStop(
#                 order_id=order.id,
#                 tracking_id=await self._generate_unique_stop_tracking(),
#                 recipient_name=stop_payload["recipient_name"],
#                 recipient_phone=stop_payload.get("recipient_phone"),
#                 recipient_email=stop_payload.get("recipient_email"),
#                 address_id=stop_payload.get("address_id"),
#                 time_window_start=stop_payload.get("time_window_start"),
#                 time_window_end=stop_payload.get("time_window_end"),
#                 delivery_preference=stop_payload.get("delivery_preference"),
#                 delivery_instructions=stop_payload.get("delivery_instructions"),
#                 sequence=stop_payload.get("sequence"),
#                 status=stop_payload.get("status", "pending"),
#                 notes=stop_payload.get("notes"),
#             )
#             self._session.add(stop)
#             await self._session.flush()

#             packages = stop_payload.get("packages", [])
#             if not packages:
#                 raise ValidationError("Each delivery stop must include at least one package")

#             for pkg_payload in packages:
#                 self._session.add(
#                     Package(    
#                         order_id=order.id,
#                         delivery_stop_id=stop.id,
#                         # package-level tracking is deprecated; stop tracking is canonical.
#                         tracking_id=None,
#                         description=pkg_payload.get("description"),
#                         length_cm=pkg_payload.get("length_cm"),
#                         width_cm=pkg_payload.get("width_cm"),
#                         height_cm=pkg_payload.get("height_cm"),
#                         weight_kg=pkg_payload.get("weight_kg"),
#                         declared_value=pkg_payload.get("declared_value"),
#                         special_handling=pkg_payload.get("special_handling"),
#                         is_fragile=pkg_payload.get("is_fragile", False),
#                         requires_signature=pkg_payload.get("requires_signature", False),
#                         safe_place_allowed=pkg_payload.get("safe_place_allowed", False),
#                         keep_upright=pkg_payload.get("keep_upright", False),
#                         status=pkg_payload.get("status", "pending"),
#                         notes=pkg_payload.get("notes"),
#                     )
#                 )
#         await self._session.flush()
#         await self._create_invoice_for_order(order)
#         return booking

#     async def _create_invoice_for_booking(self, booking: Booking) -> None:
#         """Create draft invoice at booking creation time; payments/transactions come later."""
#         invoice_service = InvoiceService(self._session, self._request)
#         issue_date = date.today()
#         await invoice_service.create_draft(
#             booking_id=booking.id,
#             organization_id=booking.organization_id,
#             customer_id=booking.customer_id,
#             issue_date=issue_date,
#             due_date=issue_date + timedelta(days=DEFAULT_INVOICE_TERMS_DAYS),
#             subtotal=Decimal(str(booking.subtotal or 0)),
#             vat_rate=Decimal("20.00"),
#             vat_amount=Decimal(str(booking.vat_amount or 0)),
#             total=Decimal(str(booking.total_amount or 0)),
#             notes=f"Auto-created from booking {booking.reference_number}",
#         )

#     async def _assert_customer_booking_allowed(self, customer_id: str) -> str | None:
#         customer = await self._session.get(User, customer_id)
#         if customer is None:
#             raise ValidationError("Customer not found")
#         if customer.role != UserRole.CUSTOMER_B2B:
#             return customer.organization_id
#         if not customer.organization_id:
#             return None

#         org = await self._session.get(Organization, customer.organization_id)
#         if org is None:
#             return customer.organization_id

#         if org.status in {OrganizationStatus.ON_HOLD, OrganizationStatus.SUSPENDED}:
#             raise ValidationError("Organization is temporarily restricted from creating new bookings")
#         return customer.organization_id

#     async def get_booking_or_404(self, booking_id: str) -> Booking:
#         booking = await self._booking_repo.get_by_id(booking_id)
#         if booking is None:
#             raise NotFoundError(resource="booking", id=booking_id)
#         return booking

#     async def get_booking_by_master_label_or_404(self, master_label_id: str) -> Booking:
#         booking = await self._booking_repo.get_by_master_label(master_label_id)
#         if booking is None:
#             raise NotFoundError(resource="booking_master_label", id=master_label_id)
#         return booking

#     async def list_delivery_stops(self, booking_id: str) -> list[DeliveryStop]:
#         await self.get_booking_or_404(booking_id)
#         return await self._booking_repo.list_stops(booking_id)

#     async def get_delivery_stop_or_404(self, booking_id: str, stop_id: str) -> DeliveryStop:
#         stop = await self._booking_repo.get_stop(booking_id, stop_id)
#         if stop is None:
#             raise NotFoundError(resource="delivery_stop", id=stop_id)
#         return stop

#     async def get_delivery_stop_by_tracking_or_404(self, tracking_id: str) -> DeliveryStop:
#         stop = await self._booking_repo.get_stop_by_tracking(tracking_id)
#         if stop is None:
#             raise NotFoundError(resource="delivery_stop_tracking", id=tracking_id)
#         return stop

#     async def list_packages_for_stop(self, booking_id: str, stop_id: str) -> list[Package]:
#         await self.get_delivery_stop_or_404(booking_id, stop_id)
#         return await self._booking_repo.list_packages_for_stop(stop_id)

#     async def add_packages_to_stop(
#         self,
#         *,
#         booking_id: str,
#         stop_id: str,
#         packages: list[dict],
#     ) -> list[Package]:
#         stop = await self.get_delivery_stop_or_404(booking_id, stop_id)
#         if not packages:
#             raise ValidationError("At least one package payload is required")

#         created: list[Package] = []
#         for pkg_payload in packages:
#             pkg = Package(
#                 booking_id=booking_id,
#                 delivery_stop_id=stop.id,
#                 tracking_id=None,
#                 description=pkg_payload.get("description"),
#                 length_cm=pkg_payload.get("length_cm"),
#                 width_cm=pkg_payload.get("width_cm"),
#                 height_cm=pkg_payload.get("height_cm"),
#                 weight_kg=pkg_payload.get("weight_kg"),
#                 declared_value=pkg_payload.get("declared_value"),
#                 special_handling=pkg_payload.get("special_handling"),
#                 is_fragile=pkg_payload.get("is_fragile", False),
#                 requires_signature=pkg_payload.get("requires_signature", False),
#                 safe_place_allowed=pkg_payload.get("safe_place_allowed", False),
#                 keep_upright=pkg_payload.get("keep_upright", False),
#                 status=pkg_payload.get("status", "pending"),
#                 notes=pkg_payload.get("notes"),
#             )
#             self._session.add(pkg)
#             created.append(pkg)
#         await self._session.flush()
#         return created
