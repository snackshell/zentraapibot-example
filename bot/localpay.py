"""What a verified receipt has to prove before it becomes money.

localverify.py answers "what does the provider say about this reference?".
This module answers the only question that matters afterwards: may we
credit it? Ported from ZentraShopBot's own zentra/localpay.py, because the
reasoning here does not get lighter for being in a starter kit — a Telebirr
receipt is a public web page, and the gap between that and a free wallet
top-up is entirely the checks below.

FAIL CLOSED, EVERYWHERE. A missing field is not a passing check. If the
provider does not say who received the money, we do not know that we did,
and "probably us" is not a basis for paying out.

THE AMOUNT IS NOT A KEY ON THIS RAIL, unlike USDT and Binance Pay. The
customer hands us the provider's own reference, which identifies the
payment by itself — so the amount is not asked to MATCH what was
requested, only to be real. What arrived is what gets credited.

SCOPE REDUCTION FROM ZENTRASHOPBOT'S OWN CHECK: no masked-account handling.
The real bot's relay sometimes redacts the middle of an account number
("2519****0207") and corroborates with the account holder's name in that
case; this starter assumes the configured receiving account is compared
against a full, unmasked account number, which is what LocalPaymentVerify
itself returns today. If your own verifier instance ever starts masking
account numbers, `same_account()` below will refuse every receipt rather
than accept a stranger's — the safe direction to be wrong in — and this
docstring is where to come look.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .localverify import TELEBIRR, mask, money, normalise

log = logging.getLogger(__name__)

# Words a provider uses for "this payment happened". Anything outside this
# set — pending, processing, failed, reversed, or a spelling nobody
# anticipated — is refused rather than interpreted.
COMPLETED = {"completed", "complete", "success", "successful", "succeeded", "paid", "done"}

# How far back a receipt may be dated and still settle a request. Generous
# on purpose — customers routinely pay first and open the request
# afterwards — but it stops a genuine, months-old payment nobody claimed
# at the time from being dredged up against a fresh request.
STALE_AFTER = timedelta(hours=24)

# Provider clocks are not our clock. A receipt dated next week is not a
# drifting clock; half an hour is tolerated, that is not.
FUTURE_SKEW = timedelta(minutes=30)

# Both rails are Ethiopian and print local time with no zone on it — a
# payment made at 09:38 in Addis prints "2026/08/22 09:38:41" and nothing
# else. Reading that as UTC would put every fresh receipt three hours in
# the FUTURE, which FUTURE_SKEW would then refuse. East Africa Time is
# +03:00 with no daylight saving, so this is a constant.
PROVIDER_TZ = timezone(timedelta(hours=3))

# Above this, a person looks before the money moves. Overpaying is normal;
# overpaying by an order of magnitude is either an expensive mistake or a
# misread amount, and both are better answered by a human than a machine.
AUTO_CREDIT_MULTIPLE = Decimal(10)
AUTO_CREDIT_HEADROOM = Decimal(5000)

DATE_FORMATS = ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")


class Refused(Exception):
    """This receipt is not going to be credited automatically, and why.

    `support` marks the cases where a person should look — the customer
    has plausibly paid and something stopped the automatic path; the
    request stays open for manual review in the dashboard, not lost.
    `operator` marks a misconfiguration rather than a payment problem.
    """

    def __init__(self, message: str, *, support: bool = False, operator: bool = False):
        super().__init__(message)
        self.support = support
        self.operator = operator


def digits(value) -> str:
    """An account number reduced to what identifies it — providers print
    the same account as "0912345678", "+251912345678" and "251912345678"
    depending on the page, so a string comparison would refuse genuine
    payments."""
    return re.sub(r"\D", "", str(value or ""))


def same_account(configured: str, reported: str) -> bool:
    """Whether two renderings of an account number are the same account,
    compared on the last nine digits — enough to distinguish accounts
    while tolerating a country code. Both sides must actually have digits:
    two empty strings are not a match, they are a missing check."""
    ours, theirs = digits(configured), digits(reported)
    if not ours or not theirs:
        return False
    tail = min(len(ours), len(theirs), 9)
    if tail < 6:
        return False
    return ours[-tail:] == theirs[-tail:]


def same_name(configured: str, reported: str) -> bool:
    """Loose name comparison: case, spacing and punctuation do not matter."""
    def flatten(text) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

    ours, theirs = flatten(configured), flatten(reported)
    if not ours or not theirs:
        return False
    return ours in theirs or theirs in ours


class Receipt:
    """One provider's answer, read into the fields this bot needs. Both
    providers describe the same event with different field names; the
    rest of the code should not have to know which."""

    def __init__(self, provider: str, data: dict):
        self.provider = provider
        self.raw = data

        if provider == TELEBIRR:
            self.reference = str(data.get("receiptNo") or "").strip()
            self.status = str(data.get("transactionStatus") or "").strip()
            self.amount = money(data.get("totalPaidAmount"))
            self.settled = money(data.get("settledAmount"))
            self.receiver_name = str(data.get("creditedPartyName") or "").strip()
            self.receiver_account = str(data.get("creditedPartyAccountNo") or "").strip()
            self.paid_at = str(data.get("paymentDate") or "").strip()
        else:
            self.reference = str(data.get("reference") or "").strip()
            # Abyssinia's endpoint only returns a record when the transfer
            # exists and carries no status field of its own — success=True
            # IS the status, and the caller already refused anything else.
            self.status = "success" if data.get("success") else ""
            self.amount = money(data.get("amount"))
            self.settled = self.amount
            self.receiver_name = str(data.get("receiver") or "").strip()
            self.receiver_account = str(data.get("receiverAccount") or "").strip()
            self.paid_at = str(data.get("date") or "").strip()

    @property
    def completed(self) -> bool:
        return self.status.strip().lower() in COMPLETED

    @property
    def credit(self) -> Decimal | None:
        """The figure to credit: what the RECEIVER got, not what the payer
        paid. Telebirr charges the sender a service fee and prints both —
        crediting the total would hand every customer a bit of somebody
        else's money. Abyssinia reports one figure and it is already the
        settled one."""
        if self.settled is not None and self.settled > 0:
            return self.settled
        return self.amount

    @property
    def paid_at_utc(self) -> datetime | None:
        """The receipt's own timestamp, or None if unreadable. Unreadable
        is NOT treated as suspicious — a format nobody anticipated is this
        bot's problem, not the customer's, and freshness is a second line
        of defence, not the one holding the door."""
        text = (self.paid_at or "").strip()
        if not text:
            return None
        stamp = None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for shape in DATE_FORMATS:
                try:
                    stamp = datetime.strptime(text, shape)
                    break
                except ValueError:
                    continue
        if stamp is None:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=PROVIDER_TZ)
        return stamp.astimezone(timezone.utc)


def check(submitted: str, answer: dict, deposit, cfg) -> Receipt:
    """Every rule except the database ones. Raises Refused, or returns a
    Receipt ready to credit.

    The database rule — has this reference already credited a deposit — is
    not here because it cannot be checked in advance without a race; it is
    enforced by db.credit_deposit()'s own UNIQUE index at write time,
    exactly like the crypto rails.
    """
    provider = answer["provider"]
    receipt = Receipt(provider, answer["data"])

    # ---- the reference we checked is the reference we were shown ----------
    wanted = normalise(submitted)
    if receipt.reference and normalise(receipt.reference) != wanted:
        log.error("Verifier answered %s for a request about %s",
                  mask(receipt.reference), mask(submitted))
        raise Refused(
            "That receipt does not match the reference you sent. Copy the "
            "reference from your receipt again.", support=True)

    # ---- the provider says it happened ------------------------------------
    if not receipt.completed:
        raise Refused(
            "That payment has not completed yet. Wait until your receipt "
            "says it is finished, then send the reference again.")

    # ---- the money came to US ----------------------------------------------
    expected_account = (cfg.telebirr_number if provider == TELEBIRR
                        else cfg.abyssinia_account)
    expected_name = (cfg.telebirr_name if provider == TELEBIRR
                     else cfg.abyssinia_name)

    if not digits(expected_account):
        log.error("%s verification ran with no configured receiver account", provider)
        raise Refused(
            "This payment method is not fully set up yet. Support has "
            "been told — please use another method.", operator=True)

    if not receipt.receiver_account and not receipt.receiver_name:
        log.error("%s receipt %s names no receiver at all", provider, mask(submitted))
        raise Refused(
            "That receipt does not say who was paid, so we cannot confirm "
            "it reached us. Contact support with your receipt.", support=True)

    account_ok = same_account(expected_account, receipt.receiver_account)
    if not account_ok:
        log.warning("%s receipt %s was paid to %s, not to %s",
                    provider, mask(submitted),
                    mask(receipt.receiver_account or receipt.receiver_name),
                    mask(expected_account))
        raise Refused(
            "That payment went to a different account, so it cannot be "
            "credited here. Check you sent it to the number shown on the "
            "payment screen.")

    if expected_name and receipt.receiver_name and not same_name(expected_name, receipt.receiver_name):
        # The account matched and the name did not — a joint account, a
        # renamed merchant, or a misread receipt. Worth a person seeing,
        # not worth refusing on its own.
        log.warning("%s receipt %s matched the account but not the name",
                    provider, mask(submitted))

    # ---- the amount is real, and it is what we actually received ----------
    paid = receipt.credit
    if paid is None:
        log.error("%s receipt %s carries no readable amount", provider, mask(submitted))
        raise Refused(
            "We could not read the amount on that payment. Contact support "
            "with your receipt.", support=True)
    if paid <= 0:
        log.error("%s receipt %s reports an amount of %s", provider, mask(submitted), paid)
        raise Refused(
            "That receipt does not show a payment amount we can credit. "
            "Contact support with your receipt.", support=True)

    expected = Decimal(str(deposit["amount_expected"]))
    ceiling = max(expected * AUTO_CREDIT_MULTIPLE, expected + AUTO_CREDIT_HEADROOM)
    if paid > ceiling:
        log.warning("%s receipt %s is %s ETB against a request for %s ETB",
                    provider, mask(submitted), paid, expected)
        raise Refused(
            f"That payment is {paid:,g} ETB, far more than the {expected:,g} "
            f"ETB this request is for. It has not been credited "
            f"automatically — support will check it and credit you.",
            support=True)

    # ---- and it belongs to this request -------------------------------------
    stamp = receipt.paid_at_utc
    if stamp is not None:
        opened = deposit["created_at"]
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if stamp < opened - STALE_AFTER:
            log.warning("%s receipt %s is dated %s, request opened %s",
                        provider, mask(submitted), stamp, opened)
            raise Refused(
                "That payment is too old to settle this request. Start a "
                "new top-up, or contact support with your receipt.", support=True)
        if stamp > now + FUTURE_SKEW:
            log.error("%s receipt %s is dated %s, which is in the future (now %s)",
                      provider, mask(submitted), stamp, now)
            raise Refused(
                "We could not make sense of the date on that payment. "
                "Contact support with your receipt.", support=True)

    return receipt
