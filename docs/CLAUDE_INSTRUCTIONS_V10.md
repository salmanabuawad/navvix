# Claude Instructions — V10 DXF CAD Engine

Implement this as the production dimension engine.

Do not use DWFX/PDF for production geometry.

## Required behavior

Input:
- clean DXF

Output:
- dimensioned DXF with real CAD DIMENSION entities

Pipeline:
1. Read LINE / LWPOLYLINE / POLYLINE.
2. Extract orthogonal wall-like segments.
3. Cluster real DXF vertices into perimeter axes.
4. Generate external chain dimensions from actual perimeter/corner axes.
5. Generate overall dimensions for each side.
6. Detect room candidates from internal axes.
7. Classify:
   - living: skip
   - service/small: skip
   - bedroom/medium: width + height
   - corridor: one clear-width dimension
8. Write real DIMENSION entities using ezdxf.add_linear_dim.
9. Save analysis JSON.

Important:
- Never draw fake dimensions on raster previews.
- Never use equal splits unless derived from DXF vertices.
- Missing dimension is better than wrong dimension.
