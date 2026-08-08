# Symphony Domain Glossary

## Project

A registered, durable binding between one Git repository, one workflow, one board, one service endpoint, and that project's worker runs. A Project's location does not change while it is registered.

## Project location

The canonical root directory of a Project's Git repository. All project-owned workflow and board paths are contained by this repository.

## Board

The issue collection owned by one Project. New issues are created in the selected Project's Board and cannot be reassigned to another Project by switching the user interface.

## Project adoption

Making an existing directory usable as a Project while preserving its contents. Adoption reuses existing Git metadata when present, initializes Git when absent, and adds only missing Symphony files.

## Project switch

A user-interface navigation from one independently running Project service to another. A switch can start the destination service, but it never retargets, redirects, or stops workers belonging to either Project.
