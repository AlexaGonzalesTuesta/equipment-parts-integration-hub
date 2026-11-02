"""AI parts recommender.

When a machine reports a fault without a usable fault code (older machines,
or a code the catalog does not know yet), the hub reads the free-text symptom
note and predicts the most likely fault. The predicted fault maps to a parts
kit in the ERP fault catalog.

Model: TF-IDF (word 1-2 grams) + multinomial logistic regression, trained on
historical service notes labeled with the fault the technician confirmed.
Predictions below the confidence threshold go to a human review queue instead
of creating an order (human in the loop, see FR-06).
"""
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from . import config as C


@dataclass
class Prediction:
    fault_code: str
    confidence: float
    top3: List[Tuple[str, float]]


class PartsRecommender:
    def __init__(self):
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=2000, C=8.0)),
        ])
        self.metrics = {}
        self.curve = []

    def fit(self, texts, labels, evaluate=True, seed=C.SEED):
        if evaluate:
            x_tr, x_te, y_tr, y_te = train_test_split(texts, labels, test_size=0.2, random_state=seed,
                                                      stratify=labels)
            self.pipeline.fit(x_tr, y_tr)
            self.metrics = self.evaluate(x_te, y_te)
            self.curve = self.threshold_curve(x_te, y_te)
        self.pipeline.fit(texts, labels)  # final model uses all history
        return self

    def predict(self, text: str) -> Prediction:
        proba = self.pipeline.predict_proba([text or ""])[0]
        classes = self.pipeline.classes_
        order = np.argsort(proba)[::-1][:3]
        top3 = [(str(classes[i]), round(float(proba[i]), 3)) for i in order]
        return Prediction(top3[0][0], top3[0][1], top3)

    def evaluate(self, texts, labels, threshold=C.AI_CONFIDENCE_THRESHOLD):
        proba = self.pipeline.predict_proba(texts)
        classes = self.pipeline.classes_
        labels = np.asarray(labels)
        top1 = classes[proba.argmax(axis=1)]
        top3 = classes[np.argsort(proba, axis=1)[:, ::-1][:, :3]]
        conf = proba.max(axis=1)
        auto = conf >= threshold
        return {
            "n_test": int(len(labels)),
            "top1_accuracy": round(float((top1 == labels).mean()), 3),
            "top3_accuracy": round(float(np.mean([labels[i] in top3[i] for i in range(len(labels))])), 3),
            "auto_order_coverage": round(float(auto.mean()), 3),
            "auto_order_precision": round(float((top1[auto] == labels[auto]).mean()), 3) if auto.any() else None,
            "threshold": threshold,
        }


    def threshold_curve(self, texts, labels, thresholds=(0.4, 0.5, 0.6, 0.65, 0.7, 0.8, 0.9)):
        """Trade-off table for the business: higher threshold = fewer wrong auto-orders, more manual work."""
        saved = self.metrics
        rows = []
        for t in thresholds:
            m = self.evaluate(texts, labels, threshold=t)
            rows.append({"threshold": t, "auto_order_coverage": m["auto_order_coverage"],
                         "auto_order_precision": m["auto_order_precision"]})
        self.metrics = saved
        return rows


def train_from_db(conn) -> PartsRecommender:
    rows = conn.execute("SELECT note_text, fault_code FROM ml_history_notes").fetchall()
    texts = [r["note_text"] for r in rows]
    labels = [r["fault_code"] for r in rows]
    return PartsRecommender().fit(texts, labels)
