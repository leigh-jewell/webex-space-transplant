import os
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

import audit_en_master
import webex_space_transplant


class WebexSpaceTransplantTests(unittest.TestCase):
    def test_find_url_in_title_drops_trailing_artifact(self):
        title = "Meraki IT - Urgent Assistance https://eurl.io/#gcm5rL8Ob(for"
        self.assertEqual(
            webex_space_transplant.find_url_in_title(title),
            "https://eurl.io/#gcm5rL8Ob",
        )

    def test_extract_shortid_from_eurl_handles_fragment_artifact(self):
        self.assertEqual(
            webex_space_transplant.extract_shortid_from_eurl(
                "https://eurl.io/#gcm5rL8Ob(for"
            ),
            "gcm5rL8Ob",
        )

    def test_clean_space_name_removes_unclosed_parenthesis_fragment(self):
        value = "Meraki IT - Urgent Assistance https://eurl.io/#gcm5rL8Ob(for"
        self.assertEqual(
            webex_space_transplant.clean_space_name(value),
            "Meraki IT - Urgent Assistance",
        )

    def test_get_ssl_cert_guidance_reports_missing_truststore(self):
        err = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        verify_paths = SimpleNamespace(cafile=None, capath=None)

        with patch.object(
            webex_space_transplant.ssl,
            "get_default_verify_paths",
            return_value=verify_paths,
        ), patch.dict(os.environ, {}, clear=True):
            guidance = webex_space_transplant.get_ssl_cert_guidance(err)

        self.assertIsNotNone(guidance)
        self.assertIn("No CA truststore is configured", guidance)
        self.assertIn("Current SSL_CERT_FILE: (unset)", guidance)
        self.assertIn("Default cafile: (none)", guidance)

    def test_get_ssl_cert_guidance_ignores_non_cert_errors(self):
        guidance = webex_space_transplant.get_ssl_cert_guidance(
            URLError("timed out")
        )
        self.assertIsNone(guidance)


class AuditEnMasterTests(unittest.TestCase):
    def test_extract_shortid_handles_fragment_artifact(self):
        self.assertEqual(
            audit_en_master.extract_shortid("https://eurl.io/#gcm5rL8Ob(for"),
            "gcm5rL8Ob",
        )

    def test_extract_shortid_reads_path_style_ids(self):
        self.assertEqual(
            audit_en_master.extract_shortid("https://eurl.io/L_Hqmwz0U"),
            "L_Hqmwz0U",
        )


if __name__ == "__main__":
    unittest.main()
