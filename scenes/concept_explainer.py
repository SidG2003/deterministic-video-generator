"""
Spike scene #1 — a CONCEPT EXPLAINER.

Demonstrates the "explain an idea" pattern: title card -> bullet reveal ->
a simple animated diagram. Uses Manim's non-LaTeX Text so we don't need a
LaTeX install for the spike.

Render (from repo root, venv active):
    SDKROOT="$(xcrun --show-sdk-path)" manim -pql scenes/concept_explainer.py ConceptExplainer
Quality flags: -ql (480p draft, fast) | -qm (720p) | -qh (1080p) | -qk (4k)
"""

from manim import (
    Scene, Text, VGroup, Circle, Arrow, Dot,
    Write, FadeIn, FadeOut, Create, Transform,
    UP, DOWN, LEFT, RIGHT, ORIGIN, BLUE, GREEN, YELLOW, WHITE,
)


class ConceptExplainer(Scene):
    def construct(self):
        # --- 1. Title card ---
        title = Text("How a Cache Works", weight="BOLD").scale(0.9)
        subtitle = Text("a 30-second explainer", slant="ITALIC").scale(0.4)
        subtitle.next_to(title, DOWN)
        self.play(Write(title))
        self.play(FadeIn(subtitle, shift=UP * 0.3))
        self.wait(0.5)
        self.play(FadeOut(subtitle), title.animate.scale(0.5).to_edge(UP))

        # --- 2. Bullet reveal ---
        bullets = VGroup(
            Text("• Store results of slow work", color=WHITE).scale(0.5),
            Text("• Serve repeats instantly", color=WHITE).scale(0.5),
            Text("• Trade memory for speed", color=WHITE).scale(0.5),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.4).next_to(title, DOWN, buff=0.8)
        for b in bullets:
            self.play(FadeIn(b, shift=RIGHT * 0.4), run_time=0.5)
        self.wait(0.5)
        self.play(FadeOut(bullets))

        # --- 3. A tiny animated diagram: user -> cache -> database ---
        user = VGroup(Circle(radius=0.5, color=BLUE), Text("User").scale(0.4))
        cache = VGroup(Circle(radius=0.5, color=YELLOW), Text("Cache").scale(0.35))
        db = VGroup(Circle(radius=0.5, color=GREEN), Text("DB").scale(0.4))
        row = VGroup(user, cache, db).arrange(RIGHT, buff=2.0)

        a1 = Arrow(user.get_right(), cache.get_left(), buff=0.1)
        a2 = Arrow(cache.get_right(), db.get_left(), buff=0.1)
        self.play(Create(user), Create(cache), Create(db))
        self.play(Create(a1), Create(a2))

        # a request "packet" hops user -> cache and stops (cache hit!)
        packet = Dot(color=WHITE).move_to(user.get_center())
        self.play(FadeIn(packet))
        self.play(packet.animate.move_to(cache.get_center()), run_time=0.8)
        hit = Text("cache hit!", color=YELLOW).scale(0.4).next_to(cache, UP)
        self.play(Write(hit))
        self.play(packet.animate.move_to(user.get_center()), run_time=0.8)
        self.play(FadeOut(packet), FadeOut(hit))
        self.wait(0.5)
