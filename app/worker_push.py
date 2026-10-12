"""Durable APNs consumer. Run separately: python -m app.worker_push."""
import asyncio
import base64
import hashlib
import json
import time
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from .config import get_settings
from .database import SessionLocal
from .models import Member, Partner, WorkerDevice, WorkerNotification, WorkerPushJob
from .workers import aware, now

_token_cache = {}


def provider_token(settings):
    fingerprint = hashlib.sha256((settings.apns_key_id + settings.apns_team_id +
        settings.apns_private_key.get_secret_value()).encode()).hexdigest()
    cached = _token_cache.get(fingerprint)
    if cached and 0 <= time.time() - cached[0] < 3000:
        return cached[1]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    def encode(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = encode(json.dumps({"alg": "ES256", "kid": settings.apns_key_id}).encode())
    claims = encode(json.dumps({"iss": settings.apns_team_id, "iat": int(time.time())}).encode())
    content = header + b"." + claims
    key = serialization.load_pem_private_key(settings.apns_private_key.get_secret_value().encode(), None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("APNs requires a P-256 private key")
    r, s = decode_dss_signature(key.sign(content, ec.ECDSA(hashes.SHA256())))
    token = (content + b"." + encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()
    _token_cache.clear()
    _token_cache[fingerprint] = (time.time(), token)
    return token


async def dispatch_batch(session, client, settings, limit=50):
    if not all((settings.apns_key_id, settings.apns_team_id, settings.apns_topic, settings.apns_private_key)):
        return 0  # Leave durable jobs pending until configured; no invented delivery.
    token = provider_token(settings)
    processed = 0
    for _ in range(limit):
        job = await session.scalar(select(WorkerPushJob).where(WorkerPushJob.status == "pending",
            WorkerPushJob.next_attempt_at <= now()).order_by(WorkerPushJob.next_attempt_at)
            .with_for_update(skip_locked=True).limit(1))
        if job is None:
            break
        notification = await session.get(WorkerNotification, job.notification_id, populate_existing=True)
        device = await session.scalar(select(WorkerDevice).where(WorkerDevice.id == job.device_id).with_for_update().execution_options(populate_existing=True))
        partner = await session.scalar(select(Partner).where(Partner.id == notification.partner_id).with_for_update().execution_options(populate_existing=True)) if notification else None
        member = await session.get(Member, (notification.partner_id, notification.user_id), populate_existing=True) if notification else None
        if (not notification or not device or not partner or not partner.access_approved
            or not member or not member.active or member.role != "worker" or not device.active
            or device.user_id != notification.user_id or notification.read
            or aware(notification.created_at) + timedelta(days=1) < now()):
            job.status = "cancelled"
        else:
            badge = await session.scalar(select(func.count()).select_from(WorkerNotification).join(Member,
                (Member.user_id == WorkerNotification.user_id) & (Member.partner_id == WorkerNotification.partner_id))
                .join(Partner, Partner.id == Member.partner_id).where(
                WorkerNotification.user_id == notification.user_id, WorkerNotification.read.is_(False),
                Member.active.is_(True), Member.role == "worker", Partner.access_approved.is_(True)))
            host = "api.sandbox.push.apple.com" if device.environment == "sandbox" else "api.push.apple.com"
            payload = {"aps": {"alert": {"title": "CrewCrate", "body": "A lead needs follow-up."},
                                  "sound": "default", "badge": badge},
                "notification_id": str(notification.id), "partner_id": str(notification.partner_id),
                "conversation_id": str(notification.conversation_id)}
            job.attempts += 1
            try:
                response = await client.post(f"https://{host}/3/device/{device.token}", json=payload,
                    headers={"authorization": "bearer " + token, "apns-topic": settings.apns_topic,
                             "apns-push-type": "alert", "apns-priority": "10",
                             "apns-expiration": str(int((aware(notification.created_at) + timedelta(days=1)).timestamp())),
                             "apns-id": str(job.id), "apns-collapse-id": str(notification.id)})
                if response.status_code == 200:
                    job.status = "accepted"  # APNs acceptance is not proof of device delivery.
                elif response.status_code == 410:
                    device.active = False
                    job.status = "failed"
                elif response.status_code in (429, 500, 503, 403):
                    job.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
                else:
                    job.status = "failed"
            except httpx.HTTPError:
                job.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
        await session.commit()
        processed += 1
    return processed


async def main():
    settings = get_settings()
    if not all((settings.apns_key_id, settings.apns_team_id, settings.apns_topic, settings.apns_private_key)):
        raise RuntimeError("Configure APNS_KEY_ID, APNS_TEAM_ID, APNS_TOPIC and APNS_PRIVATE_KEY before starting the worker")
    async with httpx.AsyncClient(http2=True, timeout=15) as client:
        while True:
            async with SessionLocal() as session:
                await dispatch_batch(session, client, settings)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
