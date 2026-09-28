"""Verify sampler output and replay compatibility across manifest filenames."""

import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
REPLAY = runpy.run_path(str(ROOT / "agent/recognition/replay_rdd_samples.py"))
SAMPLER = runpy.run_path(str(ROOT / "agent/recognition/rdd_sampler.py"))
MANIFEST_NAME = SAMPLER["MANIFEST_NAME"]


class SamplerLogExportTests(unittest.TestCase):
    def test_sample_log_is_exportable_and_keeps_its_image_references(self):
        with tempfile.TemporaryDirectory() as directory:
            sample_dir = Path(directory) / "debug" / "RedDotDetector_samples"
            with patch.dict(os.environ, {
                "RDD_SAMPLE": "all",
                "RDD_SAMPLE_DIR": str(sample_dir),
                "RDD_SAMPLE_INTERVAL": "0",
                "RDD_SAMPLE_MAX": "100",
            }):
                sampler = SAMPLER["RddSampler"](lambda: str(sample_dir))
                sampler.record(node="LogExport_Test", roi=(0, 0, 8, 8), result="miss",
                               images={"roi_crop": np.zeros((8, 8, 3), dtype=np.uint8)},
                               meta={"confidence": 0.25})

            manifest = sample_dir / "custom.log.samples.jsonl.log"
            self.assertEqual(MANIFEST_NAME, manifest.name)
            entries = REPLAY["_load_entries"](str(sample_dir))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["confidence"], 0.25)
            self.assertEqual(len(entries[0]["files"]), 1)
            self.assertTrue((sample_dir / entries[0]["files"][0]).is_file())
            self.assertFalse((sample_dir / "samples.jsonl.log").exists())

    def test_replay_merges_all_generations_without_changing_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            sample_dir = Path(directory)
            names = ("samples.jsonl", "samples.jsonl.log", "custom.log.samples.jsonl.log")
            originals = {}
            for generation, name in enumerate(names):
                text = json.dumps({"generation": generation}) + "\n"
                (sample_dir / name).write_text(text, encoding="utf-8")
                originals[name] = text

            self.assertEqual(REPLAY["_load_entries"](directory),
                             [{"generation": i} for i in range(3)])
            for name, text in originals.items():
                self.assertEqual((sample_dir / name).read_text(encoding="utf-8"), text)

    def test_replay_accepts_each_legacy_manifest_by_itself(self):
        for name in ("samples.jsonl", "samples.jsonl.log"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / name).write_text('{"legacy": true}\n', encoding="utf-8")
                self.assertEqual(REPLAY["_load_entries"](directory), [{"legacy": True}])


if __name__ == "__main__":
    unittest.main()
