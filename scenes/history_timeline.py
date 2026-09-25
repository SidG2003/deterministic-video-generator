"""
Spike scene #2 — a HISTORY / STORY told DIAGRAMMATICALLY.

This is the honest test of the "story" half of your use case: Manim tells
history well as an animated timeline/infographic, NOT as cinematic scenes with
people. Watch this render and judge whether that visual language fits your
story content.

Render:
    SDKROOT="$(xcrun --show-sdk-path)" manim -pql scenes/history_timeline.py HistoryTimeline
"""

from manim import (
    Scene, Text, VGroup, Line, Dot, Rectangle,
    Write, FadeIn, FadeOut, Create, GrowFromCenter,
    UP, DOWN, LEFT, RIGHT, BLUE, YELLOW, WHITE, GREY,
)

EVENTS = [
    ("1969", "ARPANET: first two nodes connect"),
    ("1983", "TCP/IP becomes the standard"),
    ("1991", "The World Wide Web goes public"),
    ("2007", "The smartphone era begins"),
]


class HistoryTimeline(Scene):
    def construct(self):
        title = Text("A Short History of the Internet", weight="BOLD").scale(0.7)
        title.to_edge(UP)
        self.play(Write(title))

        # The timeline spine
        spine = Line(LEFT * 5.5, RIGHT * 5.5, color=GREY).shift(DOWN * 0.5)
        self.play(Create(spine))

        n = len(EVENTS)
        xs = [-5.5 + (11.0 * i / (n - 1)) for i in range(n)]

        for i, ((year, label), x) in enumerate(zip(EVENTS, xs)):
            dot = Dot([x, -0.5, 0], color=YELLOW, radius=0.09)
            year_t = Text(year, weight="BOLD", color=BLUE).scale(0.45)
            year_t.next_to(dot, DOWN, buff=0.25)

            # alternate labels above/below to avoid crowding
            card = VGroup(
                Rectangle(width=2.6, height=0.9, color=WHITE, fill_opacity=0.05),
                Text(label, color=WHITE).scale(0.3),
            )
            direction = UP if i % 2 == 0 else DOWN
            offset = 1.6 if i % 2 == 0 else 1.9
            card.move_to([x, -0.5 + (offset if direction is UP else -offset), 0])
            connector = Line(dot.get_center(), card.get_bottom() if direction is UP
                             else card.get_top(), color=GREY)

            self.play(GrowFromCenter(dot), FadeIn(year_t), run_time=0.4)
            self.play(Create(connector), FadeIn(card, shift=direction * 0.2),
                      run_time=0.5)

        self.wait(1.0)
