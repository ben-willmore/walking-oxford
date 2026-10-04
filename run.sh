#!/bin/bash

# Mac-only wrapper script

# This will fetch exported workout data from iCloud (or your Downloads
# folder), unzip it, and then run convert.sh and convert-svg.sh to
# generate both a KML file for upload to Google My Maps, and
# an SVG format map which can be edited in graphics programs such as
# Inkscape.

set -e

rm -rf apple_health_export.bak
if [[ -d apple_health_export ]]; then
  mv apple_health_export apple_health_export.bak
fi

DOWNLOADS_EXPORT="$HOME/Downloads/export.zip"
ICLOUD_EXPORT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/export.zip"
ICLOUD_SYNC_TRIGGER="$HOME/Library/Mobile Documents/com~apple~CloudDocs/.walking-oxford-sync"

if [[ -e "$DOWNLOADS_EXPORT" ]]; then
  echo "Getting export.zip from Downloads"
  mv "$DOWNLOADS_EXPORT" .
elif [[ -e "$ICLOUD_EXPORT" ]]; then
  echo "Getting export.zip from iCloud"
  mv "$ICLOUD_EXPORT" .
else
  echo "export.zip not found in Downloads or iCloud"
  touch "$ICLOUD_SYNC_TRIGGER"
  echo -n "Waiting for export.zip in iCloud (press any key to skip)"
  skipped=false
  while [[ ! -e "$ICLOUD_EXPORT" ]]; do
    if read -r -t 1 -n 1; then
      skipped=true
      break
    fi
    echo -n "."
  done
  echo
  if [[ "$skipped" == true ]]; then
    echo "Skipped waiting for export.zip; restoring existing data"
    if [[ -d apple_health_export.bak ]]; then
      rm -rf apple_health_export
      mv apple_health_export.bak apple_health_export
    else
      echo "No existing Apple Health export to process"
      exit 1
    fi
  else
    echo "Getting export.zip from iCloud"
    cp "$ICLOUD_EXPORT" .
  fi
fi

if [[ -e export.zip ]]; then
  unzip -o export.zip
fi
./convert.sh
open .

./convert-svg.sh
#open ./walking-oxford.svg
