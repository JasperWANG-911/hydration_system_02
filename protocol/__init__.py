"""Shared DRIP BLE wire format: event codec + PicoRelay framing.

The single source of truth for the bytes exchanged between the Pico W
firmware (sender) and the backend ingest endpoint (receiver). The
firmware flashes ``event_codec.py`` and ``framing.py`` as flat modules;
the backend imports them as ``protocol.event_codec`` / ``protocol.framing``.
"""
