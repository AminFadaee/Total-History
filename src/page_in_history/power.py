import hashlib
import json
import logging
import pathlib

import numpy as np

from page_in_history.http import Http

logger = logging.getLogger(__name__)

REVISION = "71a9d2a7c389b1a812afc3cc1668f8c877681b00"
REPOSITORY = f"https://huggingface.co/Xenova/bart-large-mnli/resolve/{REVISION}/{{}}"
MAX_TOKENS = 256
PAD_ID = 1
PROGRESS_EVERY = 100
RULER, CONSORT = 0, 1
HYPOTHESES = (
    "{name} reigned as the monarch or was the head of state or supreme leader of {civilization}.",
    "{name} was only the spouse or consort of a ruler of {civilization}.",
    "{name} was a minister, general, official, regent, or noble serving {civilization}, not its ruler.",
    "{name} ruled a different state, not {civilization}.",
    "{name} was a scholar, cleric, artist, or private person.",
)


class PowerModel:
    """A natural-language-inference model (BART large, trained on MultiNLI; the ONNX export pinned to one revision, run
    with onnxruntime on the CPU) that reads what the sources say of a person and weighs five accounts of them against
    each other: ruler of the civilization, consort, servant of it, ruler of another state, or private person, giving
    each its share."""

    def __init__(self, http: Http):
        import onnxruntime
        from tokenizers import Tokenizer

        files = {name: http.download(REPOSITORY.format(path), f"bart-large-mnli-{REVISION[:12]}-{name}")
                 for name, path in (("model.onnx", "onnx/model.onnx"), ("tokenizer.json", "tokenizer.json"),
                                    ("config.json", "config.json"))}
        self.tokenizer = Tokenizer.from_file(str(files["tokenizer.json"]))
        self.tokenizer.enable_padding(pad_id=PAD_ID, pad_token="<pad>")
        self.tokenizer.enable_truncation(MAX_TOKENS)
        labels = json.loads(pathlib.Path(files["config.json"]).read_text())["label2id"]
        self.entailment = {label.lower(): int(index) for label, index in labels.items()}["entailment"]
        self.session = onnxruntime.InferenceSession(str(files["model.onnx"]), providers=["CPUExecutionProvider"])
        self.inputs = {node.name for node in self.session.get_inputs()}

    def shares(self, text: str, name: str, civilization: str) -> list[float]:
        encoded = self.tokenizer.encode_batch([(text, hypothesis.format(name=name, civilization=civilization))
                                               for hypothesis in HYPOTHESES])
        feed = {"input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
                "attention_mask": np.array([item.attention_mask for item in encoded], dtype=np.int64)}
        logits = self.session.run(None, {key: value for key, value in feed.items() if key in self.inputs})[0][:, self.entailment]
        shares = np.exp(logits - logits.max())
        return [round(float(share), 4) for share in shares / shares.sum()]


class PowerScores:
    """The model's shares kept with the data, one per civilization, person and the text read about them, so a build gives the
    same key figures on any machine and the model runs only for people it has not read before."""

    def __init__(self, path: pathlib.Path, http: Http):
        self.path = path
        self.http = http
        self.saved: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}
        self.used: dict[str, list[float]] = {}
        self.model: PowerModel | None = None
        self.read = 0

    def shares(self, civilization: str, name: str, text: str) -> list[float]:
        key = hashlib.sha1(f"{civilization}\n{name}\n{text}".encode()).hexdigest()
        if not isinstance(self.saved.get(key), list):
            self.model = self.model or PowerModel(self.http)
            self.saved[key] = self.model.shares(text, name, civilization)
            self.read += 1
            if self.read % PROGRESS_EVERY == 0:
                logger.info("Read %d new people", self.read)
        self.used[key] = self.saved[key]
        return self.used[key]

    def save(self) -> None:
        self.path.write_text(json.dumps(dict(sorted(self.used.items())), indent=0) + "\n")
        logger.info("Kept the model's reading of %d people", len(self.used))
