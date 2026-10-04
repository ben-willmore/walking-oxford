#!/bin/bash

# This will produce an SVG file called walking.svg
# containing all the routes with a pretty bearing-based colormap.

python3 gpx_to_kml.py \
  --include "51.698,-1.327" "51.813,-1.157" \
  --min-distance-metres 0.1 \
  --activity-type walking \
  --colormap rainbow \
  --color-order bearing \
  --color-center 51.7519,-1.2577 \
  --report-total-walking-distance \
  --include-markers \
  --route-marker-size 4 \
  --stroke-width 2 \
  --segment-opacity 0.6 \
  --output-format svg \
  --output walking.svg
