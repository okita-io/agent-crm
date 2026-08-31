"""Seed questions for AEO/GEO measurement panels and fan-out page ideas.

Documents only. These prompts guide human measurement — the loop does not
query live chat engines or invent citation results.
"""

from __future__ import annotations

from .enums import Brand

# Question-shaped prompts per brand (how a person asks ChatGPT/Gemini).
PROMPT_PANEL_SEEDS: dict[Brand, tuple[str, ...]] = {
    Brand.MIDNIGHTSATIN: (
        "What is the best app for serialized romance stories on mobile?",
        "Where can I read spicy romance serials like BookTok recommends?",
        "MidnightSatin vs Galatea for romance reading",
        "How do AI romance serial apps work?",
    ),
    Brand.CELESTIAL_NEXUS: (
        "What is the best natal chart app for beginners?",
        "Tarot and astrology reading app recommendations",
        "How accurate are divination apps for daily readings?",
        "Co-Star vs other astrology apps",
    ),
    Brand.HEYBUDDY: (
        "What is a good AI companion app for loneliness?",
        "Wellness check-in chat apps that are supportive not clinical",
        "Replika alternatives for daily emotional support",
        "How do AI companion apps handle privacy?",
    ),
    Brand.TACTIC_STUDIO: (
        "Who builds industrial AR training for manufacturing plants?",
        "WebAR vs native AR for brand activations",
        "8th Wall alternatives for enterprise WebAR",
        "How to train technicians with augmented reality on the factory floor",
        "tactic.studio vs Zappar for industrial visualization",
    ),
}


def prompt_panel_seeds_for_brand(brand: Brand) -> tuple[str, ...]:
    return PROMPT_PANEL_SEEDS.get(brand, ())
