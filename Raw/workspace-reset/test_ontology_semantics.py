import unittest

import ontology_notebook_support as support


def fixture():
    parts = [
        support._encode_part("database.tmdl", "database\n\tcompatibilityLevel: 1000000\n"),
        support._encode_part("model.tmdl", "model Model\nref namespace default\n"),
        support._encode_part("namespaces/default.tmdl", "namespace default\n"),
        support._encode_part("tables/source.tmdl", "table source\n\tannotation OperatorContent = retain\n"),
        support._encode_part("custom.json", '{"operator":"retain"}'),
    ]
    for name, metadata in support._project_semantics().items():
        if name in {"WeatherObservation", "WeatherForecast", "WeatherAreaMetric"}:
            continue
        properties = metadata["properties"]
        text = f"entity {name}\n\tbackingTable: source\n\tkeyProperty: {next(iter(properties))}\n"
        text += "".join(
            f"\tproperty {prop}\n\t\tdataType: string\n\t\tbackingConfiguration\n"
            f"\t\t\tvalueColumn: source.{prop}\n"
            for prop in properties
        )
        parts.append(support._encode_part(f"entities/{name}.tmdl", text))
    return parts


class OntologySemanticsTests(unittest.TestCase):
    def test_enrichment_is_additive_idempotent_and_preserves_bindings(self):
        before = fixture()
        after = support._enrich_project_semantics(before)
        self.assertEqual(after, support._enrich_project_semantics(after))
        support._verify_definition(before, after)
        indexed = support._parts_by_path(after)
        for part in before:
            if not part["path"].startswith("entities/") and part["path"] != "model.tmdl":
                self.assertEqual(part, indexed[part["path"]])
        for name in ("signal_master", "instruments", "WorkOrder", "WeatherLocation"):
            text = support._decode_part(indexed[f"entities/{name}.tmdl"])
            self.assertIn("/// ", text)
            self.assertIn("\tsynonym ", text)
        for path, part in indexed.items():
            if path.startswith("entities/"):
                text = support._decode_part(part)
                for prop, start, _, _ in support._object_spans(text, "property", 4):
                    self.assertTrue(support._semantic_description(text, start, "\t"), f"{path}:{prop}")
        self.assertNotIn("entities/WeatherObservation.tmdl", indexed)

    def test_operator_descriptions_annotations_and_synonyms_survive(self):
        parts = fixture()
        path = "entities/equipment.tmdl"
        index = next(i for i, part in enumerate(parts) if part["path"] == path)
        text = support._decode_part(parts[index])
        text = "/// Operator equipment description.\n" + text.replace(
            "entity equipment\n", "entity equipment\n\tannotation queryHint = Operator hint\n\tsynonym 'Machine'\n")
        parts[index] = support._encode_part(path, text)
        after = support._enrich_project_semantics(parts)
        support._verify_definition(parts, after)
        actual = support._decode_part(support._parts_by_path(after)[path])
        self.assertTrue(actual.startswith("/// Operator equipment description.\n"))
        self.assertIn("annotation queryHint = Operator hint", actual)
        self.assertEqual(1, actual.count("\tsynonym 'Machine'\n"))

    def test_tmdl_settings_precede_metadata_child_objects(self):
        parts = support._parts_by_path(support._enrich_project_semantics(fixture()))
        text = support._decode_part(parts["entities/equipment.tmdl"])
        self.assertLess(text.index("\tkeyProperty:"), text.index("\tsynonym "))
        for _, _, _, block in support._object_spans(text, "property", 4):
            if "\t\tsynonym " in block:
                self.assertLess(block.index("\t\tdataType:"), block.index("\t\tsynonym "))
        support._verify_definition(list(parts.values()), support._enrich_project_semantics(list(parts.values())))

    def test_rules_are_explicit_scoped_tmdl_and_do_not_certify_execution(self):
        parts = support._parts_by_path(support._enrich_project_semantics(fixture()))
        rules = [path for path in parts if path.startswith("rules/")]
        self.assertEqual(6, len(rules))
        model = support._decode_part(parts["model.tmdl"])
        for name, statement, references in support._project_business_rules():
            rule = support._decode_part(parts[f"rules/{name}.tmdl"])
            self.assertIn(f"ref rule {name}", model)
            self.assertIn(f"\tstatement: {statement}", rule)
            for entity, properties in references.items():
                self.assertIn(f"\truleReferencedEntity {entity}", rule)
                for prop in properties:
                    self.assertIn(f"\t\truleReferencedProperty {prop}", rule)

    def test_readback_cannot_silently_drop_descriptions_or_synonyms(self):
        before = support._enrich_project_semantics(fixture())
        path = "entities/signal_master.tmdl"
        for line_prefix in ("/// ", "\t/// ", "\tsynonym "):
            after = list(before)
            index = next(i for i, part in enumerate(after) if part["path"] == path)
            text = support._decode_part(after[index])
            lines = text.splitlines(keepends=True)
            lines.pop(next(i for i, line in enumerate(lines) if line.startswith(line_prefix)))
            after[index] = support._encode_part(path, "".join(lines))
            with self.assertRaisesRegex(RuntimeError, "retain"):
                support._verify_definition(before, after)

    def test_conflicting_owned_rule_fails_without_overwriting_it(self):
        parts = support._enrich_project_semantics(fixture())
        path = "rules/Hydro_AnyBadSampleInWindow.tmdl"
        index = next(i for i, part in enumerate(parts) if part["path"] == path)
        parts[index] = support._encode_part(path, "rule Hydro_AnyBadSampleInWindow\n\tstatement: Operator rule\n")
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            support._enrich_project_semantics(parts)


if __name__ == "__main__":
    unittest.main()
