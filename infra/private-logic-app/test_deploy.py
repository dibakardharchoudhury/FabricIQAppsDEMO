import unittest
from unittest.mock import patch

import deploy


class StoragePolicyGateTests(unittest.TestCase):
    def test_existing_disabled_storage_stops_before_mutations(self):
        with patch.object(deploy, "az", return_value=[
            {"name": deploy.STORAGE, "allowSharedKeyAccess": False}
        ]) as cli:
            with self.assertRaisesRegex(RuntimeError, "No deployment changes were made"):
                deploy.main()
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(cli.call_args.args[:3], ("storage", "account", "list"))

    def test_policy_modified_storage_stops_before_helper_or_key_access(self):
        def cli(*args):
            if args[:3] == ("storage", "account", "show"):
                return {"allowSharedKeyAccess": False}
            if args[:3] == ("deployment", "group", "create"):
                return {"properties": {"outputs": {
                    "storageId": {"value": f"{deploy.BASE}/providers/Microsoft.Storage/storageAccounts/{deploy.STORAGE}"}
                }}}
            return []

        with patch.object(deploy, "az", side_effect=cli) as calls, \
                patch.object(deploy, "cleanup") as cleanup, \
                patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "No publishing identity was created"):
                deploy.main()
        cleanup.assert_called_once()
        self.assertFalse(any("private-logicapp-helper" in call.args for call in calls.call_args_list))
        self.assertFalse(any("listKeys" in str(call.args) for call in calls.call_args_list))


if __name__ == "__main__":
    unittest.main()
