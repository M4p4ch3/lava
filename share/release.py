#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Copyright (C) 2019 Linaro Limited
#
# Author: Remi Duraffort <remi.duraffort@linaro.org>
#
# This file is part of LAVA.
#
# LAVA is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses>.

import argparse
import contextlib
import itertools
import os
import requests
import subprocess
import shlex
import sys
import time


#############
# Constants #
#############
GITLAB_API = "https://git.lavasoftware.org/api/v4/projects/2"
REGISTRY = "hub.lavasoftware.org/lava/lava"
COLORS = {
    "blue": "\x1b[1;34;40m",
    "purple": "\x1b[1;35;40m",
    "red": "\x1b[1;31;40m",
    "white": "\x1b[1;37;40m",
    "yellow": "\x1b[1;33;40m",
    "reset": "\x1b[0m",
}


###########
# Helpers #
###########
def run(cmd, options, env=None):
    print(
        "%s[%02d] $ %s%s%s"
        % (COLORS["blue"], options.count, COLORS["white"], cmd, COLORS["reset"])
    )
    if options.steps and options.skip < options.count:
        try:
            input()
        except EOFError:
            options.steps = False
    ret = 0
    if options.skip >= options.count:
        print("-> skip")
    elif not options.dry_run:
        if env is not None:
            env = {**os.environ, **env}
        ret = subprocess.call(shlex.split(cmd), env=env)
    options.count += 1
    print("")
    if ret != 0:
        raise Exception("Unable to run '%s', returned %d" % (cmd, ret))


def wait_pipeline(options, commit):
    # Wait for the pipeline to finish
    while True:
        ret = requests.get(GITLAB_API + "/repository/commits/" + commit)
        status = ret.json().get("last_pipeline", {}).get("status", "")
        if status == "success":
            break
        elif status == "failed":
            raise Exception("The pipeline failed")
        elif status == "canceled":
            raise Exception("The pipeline was canceled")
        elif status == "skipped":
            raise Exception("The pipeline was skipped")
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(10)


############
# Handlers #
############
def handle_prepare(options):
    # Generate the debian changelog
    run(
        'gbp dch --new-version="%s-1" --id-length=9 --release --commit --commit-msg="LAVA Software %s release"'
        % (options.version, options.version),
        options,
    )
    # Create the git tag
    run(
        'git tag --annotate --message="LAVA Software %s release" --sign -u release@lavasoftware.org %s'
        % (options.version, options.version),
        options,
    )


def handle_build(options):
    run(".gitlab-ci/build/amd64/pkg-debian-10.sh", options)
    run(".gitlab-ci/build/doc.sh", options)


def handle_test(options):
    run(".gitlab-ci/analyze/black.sh", options)
    run(".gitlab-ci/analyze/job-schema.sh", options)
    run(".gitlab-ci/analyze/pylint.sh", options)
    run(".gitlab-ci/test/dispatcher-debian-10.sh", options)
    run(".gitlab-ci/test/server-debian-10.sh", options)


def handle_push(options):
    # Push the commit and wait for the CI
    run("git push origin master", options)
    commit = (
        subprocess.check_output(["git", "rev-parse", "origin/master"])
        .decode("utf-8")
        .rstrip("\n")
    )

    print("%s# wait for CI%s" % (COLORS["purple"], COLORS["reset"]))
    if not options.dry_run and not options.skip >= options.count:
        wait_pipeline(options, commit)
    print("done\n")

    # The CI was a success so we can push the tag
    run("git push --tags origin master", options)


def handle_publish(options):
    # Check that the CI was a success
    print("%s# wait for CI%s" % (COLORS["purple"], COLORS["reset"]))
    if not options.dry_run and not options.skip >= options.count:
        commit = (
            subprocess.check_output(["git", "rev-parse", options.version])
            .decode("utf-8")
            .rstrip("\n")
        )
        wait_pipeline(options, commit)
    print("done\n")

    for name in ["buster", "stretch-backports"]:
        print("%s# sign %s .deb%s" % (COLORS["purple"], name, COLORS["reset"]))
        run(
            "scp lavasoftware.org:/home/gitlab-runner/repository/current-release/dists/%s/Release Release"
            % name,
            options,
        )
        run(
            "gpg -u C87D63FD935535CFB0CAF5C2A791358F2E49B100 -a --detach-sign Release",
            options,
        )
        run("scp Release.asc lavasoftware.org:~/Release.gpg", options)
        run(
            "ssh -t lavasoftware.org sudo mv ~/Release.gpg /home/gitlab-runner/repository/current-release/dists/%s/Release.gpg"
            % name,
            options,
        )
        run(
            "ssh -t lavasoftware.org sudo chown gitlab-runner:gitlab-runner /home/gitlab-runner/repository/current-release/dists/%s/Release.gpg"
            % name,
            options,
        )
        if not options.dry_run and not options.skip >= options.count:
            with contextlib.suppress(FileNotFoundError):
                os.unlink("Release")
            with contextlib.suppress(FileNotFoundError):
                os.unlink("Release.asc")

    print("%s# publish the new repository%s" % (COLORS["purple"], COLORS["reset"]))
    # TODO: move the old-release directory
    run(
        "ssh -t lavasoftware.org 'cd /home/gitlab-runner/repository && sudo ln -snf current-release release'",
        options,
    )

    # Pull/Push the docker images
    for (name, arch) in itertools.product(
        ["dispatcher", "server"], ["aarch64", "amd64"]
    ):
        print(
            "%s# push docker images for (%s, %s)%s"
            % (COLORS["purple"], name, arch, COLORS["reset"])
        )
        run(
            "docker pull %s/%s/lava-%s:%s" % (REGISTRY, arch, name, options.version),
            options,
        )
        run(
            "docker tag %s/%s/lava-%s:%s lavasoftware/%s-lava-%s:%s"
            % (REGISTRY, arch, name, options.version, arch, name, options.version),
            options,
        )
        run(
            "docker push lavasoftware/%s-lava-%s:%s" % (arch, name, options.version),
            options,
        )
        run(
            "docker tag %s/%s/lava-%s:%s lavasoftware/%s-lava-%s:latest"
            % (REGISTRY, arch, name, options.version, arch, name),
            options,
        )
        run("docker push lavasoftware/%s-lava-%s:latest" % (arch, name), options)

    print("%s# push docker manifests%s" % (COLORS["purple"], COLORS["reset"]))
    for name in ["dispatcher", "server"]:
        run(
            "docker manifest create lavasoftware/lava-%s:%s lavasoftware/aarch64-lava-%s:%s lavasoftware/amd64-lava-%s:%s"
            % (name, options.version, name, options.version, name, options.version),
            options,
            env={"DOCKER_CLI_EXPERIMENTAL": "enabled"},
        )
        run(
            "docker manifest push --purge lavasoftware/lava-%s:%s"
            % (name, options.version),
            options,
            env={"DOCKER_CLI_EXPERIMENTAL": "enabled"},
        )
        run(
            "docker manifest create lavasoftware/lava-%s:latest lavasoftware/aarch64-lava-%s:latest lavasoftware/amd64-lava-%s:latest"
            % (name, name, name),
            options,
            env={"DOCKER_CLI_EXPERIMENTAL": "enabled"},
        )
        run(
            "docker manifest push --purge lavasoftware/lava-%s:latest" % (name),
            options,
            env={"DOCKER_CLI_EXPERIMENTAL": "enabled"},
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-a",
        "--actions",
        default="prepare,build,test,push,publish",
        help="comma seperated list of actions",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        default=False,
        help="do not run any command",
    )
    parser.add_argument(
        "--steps", action="store_true", default=False, help="Run step by step"
    )
    parser.add_argument("--skip", type=int, default=0, help="Skip some steps")
    parser.add_argument("version", type=str, help="new version")

    # Parse the command line
    options = parser.parse_args()

    handlers = {
        "prepare": handle_prepare,
        "build": handle_build,
        "test": handle_test,
        "push": handle_push,
        "publish": handle_publish,
    }

    first = True
    options.count = 1
    for action in options.actions.split(","):
        if action in handlers:
            if not first:
                print("")
            print("%s%s%s" % (COLORS["yellow"], action.capitalize(), COLORS["reset"]))
            print("%s%s%s" % (COLORS["yellow"], "-" * len(action), COLORS["reset"]))
            try:
                handlers[action](options)
            except Exception as exc:
                print("%sexception: %s%s" % (COLORS["red"], str(exc), COLORS["reset"]))
                raise
                return 1
        else:
            raise NotImplementedError("Action '%s' does not exists" % action)
        first = False


if __name__ == "__main__":
    sys.exit(main())
