from pathlib import Path
import re
import unittest


class SchemaAuthorityTests(unittest.TestCase):
    def test_table_definitions_have_one_authority(self):
        holding = Path(__file__).resolve().parents[1] / "holding"
        offenders = []
        for path in holding.glob("*.py"):
            if path.name == "schema.py":
                continue
            if re.search(r"CREATE\s+TABLE", path.read_text(encoding="utf-8"), re.IGNORECASE):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "Move table definitions to holding/schema.py")


if __name__ == "__main__":
    unittest.main()
