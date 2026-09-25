"""
Director — interprets a validated Video IR against a Manim Scene.

Per beat:
  1. build independent elements (text/node/dot)
  2. run the layout engine to position them (measured, no overlap)
  3. build + place dependent elements (connectors, anchored items)
  4. play animation steps (outer=sequential, inner=concurrent)
  5. hold, then clear the stage for the next beat
"""

from __future__ import annotations

import random

from manim import (
    Create,
    FadeIn,
    FadeOut,
    GrowFromCenter,
    LaggedStart,
    Mobject,
    ReplacementTransform,
    Scene,
    Transform,
    TransformMatchingTex,
    Write,
    UP,
)

from .elements import build_connector, build_element
from .ir import Animation, Beat, Video, DEPENDENT_TYPES
from .layout import apply_layout, place_dependent
from .style import Style


class Director:
    def __init__(self, video: Video, style: Style):
        self.video = video
        self.style = style

    def run(self, scene: Scene) -> None:
        random.seed(self.video.seed)  # determinism hook for future generative bits
        scene.camera.background_color = self.style.bg
        for beat in self.video.beats:
            self._render_beat(scene, beat)

    def _render_beat(self, scene: Scene, beat: Beat) -> None:
        registry: dict[str, Mobject] = {}

        # 1. independent elements
        for el in beat.elements:
            if el.type not in DEPENDENT_TYPES:
                registry[el.id] = build_element(el, self.style)

        # 2. layout
        apply_layout(beat.layout, registry)

        # 3. dependent + anchored elements
        for el in beat.elements:
            if el.type in DEPENDENT_TYPES:
                registry[el.id] = build_connector(el, registry, self.style)
            else:
                place_dependent(el.type, el.props, registry[el.id], registry)

        # 4. animation steps
        for step in beat.animations:
            anims = [self._build_anim(a, registry) for a in step]
            run_time = max((a.run_time for a in step if a.run_time), default=self.style.run_time)
            scene.play(*anims, run_time=run_time)

        # 5. hold + clear
        scene.wait(beat.hold)
        if scene.mobjects:
            scene.play(*[FadeOut(m) for m in scene.mobjects], run_time=0.4)

    def _build_anim(self, a: Animation, registry: dict[str, Mobject]):
        m = registry[a.target]
        if a.type == "write":
            return Write(m)
        if a.type == "create":
            return Create(m)
        if a.type == "fade_in":
            return FadeIn(m, shift=UP * 0.3)
        if a.type == "fade_out":
            return FadeOut(m)
        if a.type == "grow":
            return GrowFromCenter(m)
        if a.type == "move":
            return m.animate.move_to(registry[a.to].get_center())
        if a.type == "reveal":
            # stagger a composite element's parts (e.g. timeline stations)
            return LaggedStart(*[FadeIn(part) for part in m], lag_ratio=0.35)
        if a.type == "transform":
            # morph source into target's shape; source mobject persists
            return Transform(m, registry[a.to])
        if a.type == "replace":
            # morph and hand identity to the target mobject
            return ReplacementTransform(m, registry[a.to])
        if a.type == "morph_tex":
            # term-by-term equation morph (both must be `math` elements)
            return TransformMatchingTex(m, registry[a.to])
        raise ValueError(f"unknown animation type '{a.type}'")
