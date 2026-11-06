"""Import all models here so Alembic autogenerate sees them."""
from app.db.models.agency import Agency
from app.db.models.agent_run import AgentRun
from app.db.models.billing import Plan, PlanInterval, Subscription, SubscriptionStatus
from app.db.models.consent_log import ConsentAction, ConsentLog, ConsentScope
from app.db.models.conversation import Conversation, ConversationChannel, ConversationStatus
from app.db.models.device_token import DeviceToken
from app.db.models.external_erasure_request import ExternalErasureRequest
from app.db.models.inbound_outbox import InboundOutbox
from app.db.models.invitation import Invitation
from app.db.models.lead import Lead, LeadStatus, LeadTemperature
from app.db.models.lead_offer import LeadOffer, LeadOfferStatus
from app.db.models.message import Message, MessageDirection, MessageRole
from app.db.models.message_template import MessageTemplate
from app.db.models.offer import Offer, OfferStatus
from app.db.models.office import Office
from app.db.models.property import Property, PropertyStatus, PropertyType
from app.db.models.user import User, UserRole
from app.db.models.visit import Visit, VisitStatus
from app.db.models.webhook_outbox import WebhookOutbox
from app.db.models.webhook_subscription import WebhookSubscription

__all__ = [
    "Agency",
    "AgentRun",
    "ConsentAction",
    "ConsentLog",
    "ConsentScope",
    "Conversation",
    "ConversationChannel",
    "ConversationStatus",
    "DeviceToken",
    "ExternalErasureRequest",
    "Invitation",
    "InboundOutbox",
    "Lead",
    "LeadOffer",
    "LeadOfferStatus",
    "LeadStatus",
    "LeadTemperature",
    "Message",
    "MessageDirection",
    "MessageRole",
    "MessageTemplate",
    "Offer",
    "OfferStatus",
    "Office",
    "Plan",
    "PlanInterval",
    "Property",
    "PropertyStatus",
    "PropertyType",
    "Subscription",
    "SubscriptionStatus",
    "User",
    "UserRole",
    "Visit",
    "VisitStatus",
    "WebhookSubscription",
    "WebhookOutbox",
]
