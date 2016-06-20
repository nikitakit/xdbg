#!/bin/bash

# Builds html, and updates the gh-pages branch

set -e
set -v

mkdir -p html
pushd html
jupyter nbconvert ../doc/*.ipynb
mv ../doc/*.html .
popd

CURRENT_HEAD=`git rev-parse --abbrev-ref HEAD`
git checkout --detach
git add html
git commit -m "Generate HTML for `git rev-parse HEAD`"
git branch -f gh-pages
git reset $CURRENT_HEAD
git checkout $CURRENT_HEAD
