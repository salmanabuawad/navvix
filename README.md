# Navvix V11 Semantic Dimension Engine

CAD-first semantic dimension engine.

Main idea:
1. Isolate the inner apartment drawing only.
2. Ignore frame, title block, legend and tables.
3. Extract real DXF wall axes.
4. Generate external chain + overall dimensions.
5. Add internal dimensions only to semantic rooms/corridors.
6. Skip living, service, balcony, perimeter recess and niches.

Run:

```bash
pip install -r requirements.txt
python -m navvix_v11 --input without.dxf --output-dir out
```
