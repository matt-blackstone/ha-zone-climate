"""Tiny REST client for talking to the test Home Assistant container.

Designed specifically for the integration test suite — does just enough
to onboard the first admin, exchange the auth code for a long-lived
access token, drive entities, and inspect state. NOT production code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

DEFAULT_CLIENT_ID = "https://e2e.test/"


def _normalise_unit(unit: Any) -> str | None:
    if unit is None:
        return None
    text = str(unit).strip().lower()
    return text or None


@dataclass
class HAClient:
    base_url: str
    access_token: str
    user_temperature_unit: str = "°C"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @property
    def is_fahrenheit(self) -> bool:
        return _normalise_unit(self.user_temperature_unit) == "°f"

    # ----- onboarding helpers (stand-alone, not tied to an instance) -----

    @staticmethod
    def wait_until_up(base_url: str, timeout: float = 120.0) -> None:
        """Block until HA answers any HTTP response on the base URL."""
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                r = requests.get(base_url + "/", timeout=2)
                if r.status_code < 500:
                    return
            except requests.RequestException as err:
                last_err = err
            time.sleep(1)
        raise TimeoutError(
            f"Home Assistant did not respond at {base_url} within {timeout}s "
            f"(last error: {last_err})"
        )

    @classmethod
    def bootstrap(
        cls,
        base_url: str,
        username: str = "e2e",
        password: str = "e2e-password",
        client_id: str = DEFAULT_CLIENT_ID,
    ) -> "HAClient":
        """Run onboarding from scratch and return an authenticated client."""
        cls.wait_until_up(base_url)
        access = _onboard(base_url, username, password, client_id)
        client = cls(base_url=base_url, access_token=access)
        client.wait_for_api()
        # Discover HA's display unit so test temperature setters can
        # convert from Celsius (the integration's internal unit) to
        # whatever the running HA instance is configured to expect.
        client.user_temperature_unit = client.fetch_temperature_unit()
        return client

    def fetch_temperature_unit(self) -> str:
        r = requests.get(
            f"{self.base_url}/api/config", headers=self.headers, timeout=10
        )
        r.raise_for_status()
        cfg = r.json()
        unit = (cfg.get("unit_system") or {}).get("temperature") or "°C"
        return unit

    # ----- instance methods -----

    def wait_for_api(self, timeout: float = 60.0) -> None:
        """Wait until /api/ responds 200 (HA finished startup)."""
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                r = requests.get(
                    self.base_url + "/api/", headers=self.headers, timeout=5
                )
                if r.status_code == 200:
                    return
            except requests.RequestException as err:
                last_err = err
            time.sleep(1)
        raise TimeoutError(
            f"Home Assistant API not ready within {timeout}s "
            f"(last error: {last_err})"
        )

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        r = requests.get(
            f"{self.base_url}/api/states/{entity_id}",
            headers=self.headers,
            timeout=10,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def all_states(self) -> list[dict[str, Any]]:
        r = requests.get(
            f"{self.base_url}/api/states", headers=self.headers, timeout=10
        )
        r.raise_for_status()
        return r.json()

    def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = data or {}
        r = requests.post(
            f"{self.base_url}/api/services/{domain}/{service}",
            json=payload,
            headers=self.headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def set_input_number(self, entity_id: str, value: float) -> None:
        self.call_service(
            "input_number",
            "set_value",
            {"entity_id": entity_id, "value": float(value)},
        )

    def set_input_boolean(self, entity_id: str, on: bool) -> None:
        self.call_service(
            "input_boolean",
            "turn_on" if on else "turn_off",
            {"entity_id": entity_id},
        )

    def force_climate_refresh(self, entity_id: str) -> None:
        """Nudge the coordinator by re-issuing the current intent.

        ``homeassistant.update_entity`` triggers a coordinator refresh on
        a CoordinatorEntity, which is exactly what we need between
        scenario steps.
        """
        self.call_service(
            "homeassistant",
            "update_entity",
            {"entity_id": entity_id},
        )

    def set_climate_hvac_mode(self, entity_id: str, hvac_mode: str) -> None:
        self.call_service(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": hvac_mode},
        )

    def set_climate_temperature(
        self,
        entity_id: str,
        temperature_c: float,
        hvac_mode: str | None = None,
    ) -> None:
        """Set a climate setpoint, given in Celsius.

        HA's ``climate.set_temperature`` interprets ``temperature`` in
        the user-display unit, so we convert from Celsius into HA's
        active unit on the way out.
        """
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "temperature": self.celsius_to_user_unit(float(temperature_c)),
        }
        if hvac_mode is not None:
            payload["hvac_mode"] = hvac_mode
        self.call_service("climate", "set_temperature", payload)

    def celsius_to_user_unit(self, value_c: float) -> float:
        if self.is_fahrenheit:
            return value_c * 9.0 / 5.0 + 32.0
        return value_c

    def user_unit_to_celsius(self, value: float) -> float:
        if self.is_fahrenheit:
            return (float(value) - 32.0) * 5.0 / 9.0
        return float(value)

    def get_climate_target_celsius(self, entity_id: str) -> float | None:
        """Read climate.target_temperature attribute, normalised to Celsius."""
        state = self.get_state(entity_id)
        if state is None:
            return None
        target = state["attributes"].get("temperature")
        if target is None:
            return None
        return self.user_unit_to_celsius(target)

    def get_climate_current_celsius(self, entity_id: str) -> float | None:
        state = self.get_state(entity_id)
        if state is None:
            return None
        current = state["attributes"].get("current_temperature")
        if current is None:
            return None
        return self.user_unit_to_celsius(current)

    def wait_for_state(
        self,
        entity_id: str,
        predicate,
        timeout: float = 30.0,
        interval: float = 0.5,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Poll until ``predicate(state_dict)`` returns truthy or timeout."""
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.get_state(entity_id)
            if last is not None:
                try:
                    if predicate(last):
                        return last
                except (KeyError, TypeError, ValueError):
                    pass
            time.sleep(interval)
        raise AssertionError(
            f"Timed out waiting for {entity_id} "
            f"({description or predicate}); last seen: {last}"
        )


# ---------- private onboarding internals ----------


def _onboard(
    base_url: str, username: str, password: str, client_id: str
) -> str:
    """Walk HA's onboarding flow and return a bearer access token."""
    onboarding_status = _get(base_url + "/api/onboarding")
    pending = {step["step"]: step for step in onboarding_status}

    # Step 1: create the first user. This implicitly logs them in.
    if not pending.get("user", {}).get("done", True):
        user_resp = _post(
            base_url + "/api/onboarding/users",
            json={
                "client_id": client_id,
                "name": "E2E",
                "username": username,
                "password": password,
                "language": "en",
            },
        )
        auth_code = user_resp.get("auth_code")
        if not auth_code:
            raise RuntimeError(
                f"onboarding/users did not return auth_code: {user_resp}"
            )
        access_token = _exchange_code_for_token(base_url, client_id, auth_code)
    else:
        # User already exists from a previous run — log in via auth flow.
        access_token = _login_existing(base_url, username, password, client_id)

    bearer = {"Authorization": f"Bearer {access_token}"}

    # Walk remaining onboarding steps so the API isn't gated.
    for step_name in ("core_config", "analytics", "integration"):
        step = pending.get(step_name)
        if step is None or step.get("done"):
            continue
        try:
            _post(
                base_url + f"/api/onboarding/{step_name}",
                json={"client_id": client_id} if step_name == "integration" else {},
                headers=bearer,
            )
        except requests.HTTPError:
            # Some HA versions auto-complete certain steps server-side.
            pass

    return access_token


def _exchange_code_for_token(
    base_url: str, client_id: str, code: str
) -> str:
    body = urlencode(
        {
            "client_id": client_id,
            "code": code,
            "grant_type": "authorization_code",
        }
    )
    r = requests.post(
        base_url + "/auth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    if "access_token" not in payload:
        raise RuntimeError(f"unexpected /auth/token response: {payload}")
    return payload["access_token"]


def _login_existing(
    base_url: str, username: str, password: str, client_id: str
) -> str:
    """Re-login when the test container is reused (post-onboarding)."""
    flow = _post(
        base_url + "/auth/login_flow",
        json={
            "client_id": client_id,
            "handler": ["homeassistant", None],
            "redirect_uri": client_id,
        },
    )
    flow_id = flow["flow_id"]
    step = _post(
        base_url + f"/auth/login_flow/{flow_id}",
        json={
            "client_id": client_id,
            "username": username,
            "password": password,
        },
    )
    if step.get("type") != "create_entry":
        raise RuntimeError(f"login_flow did not yield create_entry: {step}")
    code = step["result"]
    return _exchange_code_for_token(base_url, client_id, code)


def _get(url: str, headers: dict[str, str] | None = None) -> Any:
    r = requests.get(url, headers=headers or {}, timeout=10)
    r.raise_for_status()
    return r.json()


def _post(
    url: str,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    r = requests.post(url, json=json or {}, headers=headers or {}, timeout=15)
    r.raise_for_status()
    return r.json()
