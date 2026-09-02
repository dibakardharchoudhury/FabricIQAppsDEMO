import unittest

from key_vault_preflight import (
    PreflightError,
    endpoint_state,
    matching_endpoint,
    public_network_is_open,
    select_connection,
    vault_name_from_uri,
)


class KeyVaultPreflightTests(unittest.TestCase):
    def test_vault_uri_requires_canonical_https_host(self):
        self.assertEqual(vault_name_from_uri("https://Demo-Vault.vault.azure.net/"), "demo-vault")
        for uri in ("http://demo-vault.vault.azure.net", "https://example.com", "demo-vault"):
            with self.subTest(uri=uri), self.assertRaises(PreflightError):
                vault_name_from_uri(uri)

    def test_public_network_must_be_enabled_and_default_allow(self):
        self.assertTrue(public_network_is_open({"properties": {}}))
        self.assertFalse(public_network_is_open({"properties": {"publicNetworkAccess": "Disabled"}}))
        self.assertFalse(public_network_is_open({"properties": {"networkAcls": {"defaultAction": "Deny"}}}))

    def test_matches_vault_endpoint_across_response_shapes(self):
        vault_id = "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/demo"
        endpoints = [
            {"targetPrivateLinkResourceId": vault_id.upper(), "targetSubresourceType": "vault"},
        ]
        self.assertIs(endpoints[0], matching_endpoint(endpoints, vault_id))

    def test_endpoint_state_accepts_nested_connection_object(self):
        endpoint = {"properties": {"provisioningState": "Succeeded", "connectionState": {"status": "Approved"}}}
        self.assertEqual(endpoint_state(endpoint), ("Succeeded", "Approved"))

    def test_connection_selection_refuses_ambiguity(self):
        one = {"id": "new", "properties": {"privateLinkServiceConnectionState": {"status": "Pending"}}}
        two = {"id": "other", "properties": {"privateLinkServiceConnectionState": {"status": "Pending"}}}
        self.assertIs(one, select_connection([one], {"old"}))
        self.assertIsNone(select_connection([one, two], {"old"}))
        self.assertIsNone(select_connection([one], {"new"}))


if __name__ == "__main__":
    unittest.main()