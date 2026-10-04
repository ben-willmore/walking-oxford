#!/bin/bash

# This will produce a KML file called walking.kml that
# can be uploaded to Google My Maps (create new map, add layer, import):
# https://www.google.com/maps/about/mymaps/

# By default, the color map will cycle through the routes in date order,
# with the most recent being in blue and today's routes in black.

python3 gpx_to_kml.py \
  --include "51.698,-1.327" "51.813,-1.157" \
  --min-distance-metres 0.5 \
  --activity-type walking \
  --colormap rainbow \
  --report-total-walking-distance \
  --output walking.kml
