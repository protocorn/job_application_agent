import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class CmpConsent:
    """Handles cookie consent by interacting with common Consent Management Platform (CMP) APIs."""

    def __init__(self, page: Any):
        self.page = page
        self.cmps = {
            'tcfapi': self._handle_tcfapi,
            'didomi': self._handle_didomi,
            'onetrust': self._handle_onetrust,
            'quantcast': self._handle_quantcast,
            'cookiebot': self._handle_cookiebot,
        }

    async def detect_and_handle(self) -> bool:
        """Detect CMP presence and attempt to accept/dismiss it.

        Strategy:
        1) Try known CMP JS APIs (fast, non-UI)
        2) Try common UI selectors for consent banners/buttons
        """
        try:
            # 1) Fast path: known JS APIs
            accepted = await self.accept_all()
            if accepted:
                return True

            # 2) UI fallbacks – common buttons/texts
            selectors = [
                # OneTrust/Generic
                'button#onetrust-accept-btn-handler',
                'button[aria-label*="Accept" i]',
                'button:has-text("Accept All")',
                'button:has-text("Accept all")',
                'button:has-text("I Accept")',
                'button:has-text("Agree")',
                'button:has-text("Allow all")',
                'button:has-text("Got it")',
                'text=/accept all cookies/i',
            ]

            for selector in selectors:
                try:
                    el = self.page.locator(selector).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        logger.info(f"✅ Dismissed CMP via UI selector: {selector}")
                        return True
                except Exception:
                    continue

            logger.debug("CMP detect_and_handle: No APIs or UI selectors succeeded")
            return False
        except Exception as e:
            logger.debug(f"CMP detect_and_handle error: {e}")
            return False

    async def accept_all(self) -> bool:
        """Attempt to accept all cookies by calling known CMP APIs."""
        for name, handler in self.cmps.items():
            try:
                if await handler():
                    logger.info(f"✅ Successfully accepted cookies via {name} API.")
                    return True
            except Exception as e:
                logger.debug(f"CMP API '{name}' failed or not present: {e}")
        logger.info("ℹ️ No known CMP API found or succeeded.")
        return False

    async def _handle_tcfapi(self) -> bool:
        """Handles IAB TCF v2.0 API."""
        script = """
        () => {
            return new Promise((resolve) => {
                if (typeof window.__tcfapi !== 'function') return resolve(false);
                window.__tcfapi('getTCData', 2, (tcData, success) => {
                    if (!success || !tcData) return resolve(false);
                    const vendorConsents = Object.keys(tcData.vendor.consents).map(k => parseInt(k, 10));
                    window.__tcfapi('setConsent', 2, () => resolve(true), {
                        consentScreen: 0,
                        consentLanguage: 'en',
                        vendorConsents: vendorConsents,
                        purposeConsents: Array.from({length: tcData.purpose.consents.length}, (_, i) => i + 1)
                    });
                });
            });
        }
        """
        return await self.page.evaluate(script)

    async def _handle_didomi(self) -> bool:
        """Handles Didomi CMP."""
        script = """
        () => {
            if (typeof window.Didomi === 'undefined' || typeof window.Didomi.setUserAgreeToAll === 'undefined') {
                return false;
            }
            window.Didomi.setUserAgreeToAll();
            return true;
        }
        """
        return await self.page.evaluate(script)

    async def _handle_onetrust(self) -> bool:
        """Handles OneTrust CMP."""
        script = """
        () => {
            if (typeof window.OneTrust === 'undefined' || typeof window.OneTrust.AllowAll === 'undefined') {
                return false;
            }
            window.OneTrust.AllowAll();
            return true;
        }
        """
        return await self.page.evaluate(script)

    async def _handle_quantcast(self) -> bool:
        """Handles Quantcast Choice."""
        script = """
        () => {
            return new Promise((resolve) => {
                if (typeof window.__qc === 'undefined' || typeof window.__qc.q === 'undefined') {
                    return resolve(false);
                }
                window.__qc.q.push({
                    'event': 'consent',
                    'consent': 'all',
                    'callback': () => resolve(true)
                });
            });
        }
        """
        return await self.page.evaluate(script)
        
    async def _handle_cookiebot(self) -> bool:
        """Handles Cookiebot CMP."""
        script = """
        () => {
            if (typeof window.Cookiebot === 'undefined' || typeof window.Cookiebot.submitCustomConsent === 'undefined') {
                return false;
            }
            window.Cookiebot.submitCustomConsent(true, true, true);
            return true;
        }
        """
        return await self.page.evaluate(script)
