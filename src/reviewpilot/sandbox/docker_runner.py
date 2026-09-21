"""Docker sandbox runner for ReviewPilot.

Manages the lifecycle of isolated Docker containers for testing
code suggestions. Containers run with strict security constraints:
no network, memory limits, non-root user, and all capabilities dropped.
"""
from __future__ import annotations

import logging
import tarfile
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

from reviewpilot.config import SandboxConfig

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of running a command in the Docker sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """Whether the command exited successfully."""
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


class DockerRunner:
    """Manages Docker containers for sandbox verification.

    Creates ephemeral containers with strict security constraints,
    copies the patched repo into them, runs tests, and tears down.
    """

    def __init__(self, config: SandboxConfig) -> None:
        """Initialize the Docker runner.

        Args:
            config: Sandbox configuration

        Raises:
            DockerError: If Docker is not available
        """
        self.config = config
        try:
            self._client = docker.from_env()
            self._client.ping()
        except DockerException as e:
            raise DockerError(
                "Docker is not available. Make sure Docker Desktop is running. "
                f"Error: {e}"
            ) from e

    def _create_tar_archive(self, source_dir: Path) -> bytes:
        """Create a tar archive of a directory for container injection.

        Args:
            source_dir: Directory to archive

        Returns:
            Tar archive as bytes
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for item in source_dir.rglob("*"):
                arcname = str(item.relative_to(source_dir))
                tar.add(str(item), arcname=arcname)
        buf.seek(0)
        return buf.read()

    def run_tests(self, repo_dir: Path) -> SandboxResult:
        """Run the test command in an isolated Docker container.

        Creates a container, copies the repo into it, executes the test
        command, captures output, and tears down the container.

        Security constraints applied:
        - --network=none (no internet access)
        - --memory limit
        - --cap-drop=ALL (drop all kernel capabilities)
        - Non-root user (1000:1000)
        - --security-opt=no-new-privileges
        - --pids-limit=128

        Args:
            repo_dir: Path to the patched repository to test

        Returns:
            SandboxResult with exit code, stdout, stderr, and timeout status
        """
        container = None
        try:
            # Ensure the image exists
            try:
                self._client.images.get(self.config.docker_image)
            except ImageNotFound:
                logger.info("Pulling Docker image: %s", self.config.docker_image)
                self._client.images.pull(self.config.docker_image)

            # Create container with security constraints
            container = self._client.containers.create(
                image=self.config.docker_image,
                command="sleep infinity",  # Keep alive while we copy files + exec
                detach=True,
                network_mode="none",
                mem_limit=self.config.memory_limit,
                pids_limit=128,
                user="1000:1000",
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                working_dir="/app",
            )

            # Start the container
            container.start()

            # Copy repo into container at /app
            tar_data = self._create_tar_archive(repo_dir)
            container.put_archive("/app", tar_data)

            # Execute test command
            logger.info("Running tests in sandbox: %s", self.config.test_command)
            exec_result = container.exec_run(
                cmd=["sh", "-c", self.config.test_command],
                workdir="/app",
                user="1000:1000",
                demux=True,  # Separate stdout/stderr
            )

            stdout = ""
            stderr = ""
            if exec_result.output:
                if isinstance(exec_result.output, tuple):
                    stdout = (exec_result.output[0] or b"").decode("utf-8", errors="replace")
                    stderr = (exec_result.output[1] or b"").decode("utf-8", errors="replace")
                else:
                    stdout = exec_result.output.decode("utf-8", errors="replace")

            return SandboxResult(
                exit_code=exec_result.exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        except Exception as e:
            logger.error("Sandbox execution failed: %s", e)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox error: {e}",
            )

        finally:
            # Always clean up the container
            if container:
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                except Exception as e:
                    logger.warning("Container cleanup failed: %s", e)

    def is_available(self) -> bool:
        """Check if Docker is available and responsive."""
        try:
            self._client.ping()
            return True
        except Exception:
            return False


class DockerError(Exception):
    """Raised when Docker operations fail."""
    pass
