---
title: CALLSHEET
emoji: 🎬
colorFrom: gray
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
---

# CALLSHEET

A render farm that reports to Grafana, and an agent that turns its telemetry
into production decisions — which shots get sacrificed so the morning review
isn't missed.

The board is the call sheet itself. Each shot is a numbered row carrying its
last rendered frame. When the agent issues an amendment the sheet is reissued on
the next colour of paper — white, blue, pink, goldenrod — which is what a film
production does when the day is revised.

Built for the Google Cloud Agentic Cinema Hackathon, Grafana partner track.
Source: https://github.com/santoshcheethiralame-dot/CallSheet
