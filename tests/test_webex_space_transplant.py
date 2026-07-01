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

    def test_main_join_from_csv_does_not_require_token(self):
        args = SimpleNamespace(
            debug=False,
            check_master_membership=False,
            master_csv=webex_space_transplant.MASTER_SPACES_CSV,
            join_from_csv="",
            join_email="person@example.com",
        )

        with patch.object(
            webex_space_transplant, "parse_args", return_value=args
        ), patch.object(
            webex_space_transplant, "configure_logging", return_value=None
        ), patch.object(
            webex_space_transplant, "print_banner"
        ), patch.object(
            webex_space_transplant, "prompt_for_valid_token"
        ) as prompt_for_valid_token, patch.object(
            webex_space_transplant, "run_join_from_csv_mode", return_value=0
        ) as run_join_from_csv_mode:
            result = webex_space_transplant.main()

        self.assertEqual(result, 0)
        self.assertFalse(prompt_for_valid_token.called)
        run_join_from_csv_mode.assert_called_once_with(
            webex_space_transplant.MISSING_MEMBERSHIP_OUTPUT_CSV,
            "person@example.com",
            webex_space_transplant.JOIN_RESULTS_OUTPUT_CSV,
        )

    def test_main_interactive_join_does_not_require_token(self):
        args = SimpleNamespace(
            debug=False,
            check_master_membership=False,
            master_csv=webex_space_transplant.MASTER_SPACES_CSV,
            join_from_csv=None,
            join_email=None,
        )

        with patch.object(
            webex_space_transplant, "parse_args", return_value=args
        ), patch.object(
            webex_space_transplant, "configure_logging", return_value=None
        ), patch.object(
            webex_space_transplant, "print_banner"
        ), patch.object(
            webex_space_transplant, "prompt_yes_no", return_value=True
        ), patch.object(
            webex_space_transplant,
            "prompt_input_with_default",
            return_value="my_join_list.csv",
        ), patch(
            "builtins.input", return_value="person@example.com"
        ), patch.object(
            webex_space_transplant, "prompt_for_valid_token"
        ) as prompt_for_valid_token, patch.object(
            webex_space_transplant, "run_join_from_csv_mode", return_value=0
        ) as run_join_from_csv_mode:
            result = webex_space_transplant.main()

        self.assertEqual(result, 0)
        self.assertFalse(prompt_for_valid_token.called)
        run_join_from_csv_mode.assert_called_once_with(
            "my_join_list.csv",
            "person@example.com",
            webex_space_transplant.JOIN_RESULTS_OUTPUT_CSV,
        )


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
