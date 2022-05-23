#!/usr/bin/env python3
import asyncio

from datetime import datetime, timedelta, timezone
from enum import Enum
import pathlib
import sys

from yapapi.golem import Golem
from yapapi.payload import vm
from yapapi.services import Service, ServiceState

examples_dir = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(examples_dir))

from utils import (
    build_parser,
    TEXT_COLOR_CYAN,
    TEXT_COLOR_DEFAULT,
    run_golem_example,
    print_env_info,
)

# the timeout after we commission our service instances
# before we abort this script
STARTING_TIMEOUT = timedelta(minutes=4)

# additional expiration margin to allow providers to take our offer,
# as providers typically won't take offers that expire sooner than 5 minutes in the future
EXPIRATION_MARGIN = timedelta(minutes=5)

computation_state = {}
completion_state = {}


class State(Enum):
    IDLE = 0
    COMPUTING = 1
    FAILURE = 2


def server_log_file_name(ip: str) -> str:
    return f"server_{ip}_logs.json"


class PerformanceService(Service):
    @staticmethod
    async def get_payload():
        return await vm.repo(
            image_hash="787b3430ee1e431fafa9925b4661414173e892d219db8b53c491636f",
            min_mem_gib=0.2,
            min_storage_gib=0.5,
        )

    async def start(self):
        # perform the initialization of the Service
        # (which includes sending the network details within the `deploy` command)
        async for script in super().start():
            yield script

        server_ip = self.network_node.ip
        output_file = server_log_file_name(server_ip)

        script = self._ctx.new_script(timeout=timedelta(minutes=1))
        script.run("/bin/bash", "-c", f"iperf3 -s -D --json --logfile /golem/output/{output_file}")
        yield script

    async def run(self):
        global computation_state
        global completion_state

        client_ip = self.network_node.ip
        neighbour_count = len(network_addresses) - 1
        computation_state[client_ip] = (State.IDLE, None)
        completion_state[client_ip] = set()

        print(f"{client_ip}: running")

        while len(completion_state[client_ip]) < neighbour_count:
            for server_ip in network_addresses:
                if server_ip == client_ip:
                    continue
                elif server_ip in completion_state[client_ip]:
                    continue
                elif server_ip not in computation_state:
                    continue

                state, computing_ip = computation_state[server_ip]
                if state != State.IDLE or computing_ip == client_ip:
                    continue

                computation_state[server_ip] = (State.COMPUTING, client_ip)
                computation_state[client_ip] = (State.COMPUTING, server_ip)
                print(f"{client_ip}: computing on {server_ip}")

                try:

                    # log_file = server_log_file_name(server_ip)
                    output_file = f"client_{client_ip}_to_server_TEST_logs.json"

                    script = self._ctx.new_script(timeout=timedelta(minutes=10))
                    script.run(
                        "/bin/bash",
                        "-c",
                        f"iperf3 -c {server_ip} --json --logfile /golem/output/{output_file}",
                    )
                    yield script

                    script = self._ctx.new_script(timeout=timedelta(minutes=3))
                    script.download_file(
                        f"/golem/output/{output_file}", f"golem/output/{output_file}"
                    )
                    # script.download_file(f"/golem/output/{log_file}", f"golem/output/{log_file}")
                    yield script

                    completion_state[client_ip].add(server_ip)
                    print(f"{client_ip}: finished on {server_ip}")

                finally:
                    computation_state[server_ip] = (State.IDLE, None)
                    computation_state[client_ip] = (State.IDLE, None)

            await asyncio.sleep(1)

        print(f"{client_ip}: finished computing")

        # keep running - nodes may want to compute on this node
        while len(completion_state) < neighbour_count or not all(
            [len(c) == neighbour_count for c in completion_state.values()]
        ):
            await asyncio.sleep(1)

    async def reset(self):
        # We don't have to do anything when the service is restarted
        pass


def generate_ip_addresses(num_instances) -> list[str]:
    base = "192.168.0."
    result = []

    if num_instances > 255:
        raise Exception(f"Cannot test more than 255 nodes in parallel")

    for i in range(1, num_instances + 1):
        host_id = str(i + 1)
        ip_address = base + host_id
        result.append(ip_address)

    return result


network_addresses = []
iperf3_server = ""


# ######## Main application code which spawns the Golem service and the local HTTP server
async def main(
    subnet_tag, payment_driver, payment_network, num_instances, running_time, instances=None
):
    async with Golem(
        budget=1.0,
        subnet_tag=subnet_tag,
        payment_driver=payment_driver,
        payment_network=payment_network,
    ) as golem:
        print_env_info(golem)

        commissioning_time = datetime.now()
        global network_addresses
        global iperf3_server

        network_addresses = generate_ip_addresses(num_instances)
        iperf3_server = "lon.speedtest.clouvider.net"

        network = await golem.create_network("192.168.0.1/24")
        cluster = await golem.run_service(
            PerformanceService,
            network=network,
            num_instances=num_instances,
            expiration=datetime.now(timezone.utc)
            + STARTING_TIMEOUT
            + EXPIRATION_MARGIN
            + timedelta(seconds=running_time),
            network_addresses=network_addresses,
        )

        instances = cluster.instances

        def still_starting():
            return any(i.state in (ServiceState.pending, ServiceState.starting) for i in instances)

        # wait until all remote http instances are started

        while still_starting() and datetime.now() < commissioning_time + STARTING_TIMEOUT:
            print(f"instances: {instances}")
            await asyncio.sleep(5)

        if still_starting():
            raise Exception(
                f"Failed to start instances after {STARTING_TIMEOUT.total_seconds()} seconds"
            )

        start_time = datetime.now()

        while datetime.now() < start_time + timedelta(seconds=running_time):
            print(instances)
            try:
                await asyncio.sleep(10)
            except (KeyboardInterrupt, asyncio.CancelledError):
                break

        cluster.stop()


if __name__ == "__main__":
    parser = build_parser("NET measurement tool")
    parser.add_argument(
        "--num-instances",
        type=int,
        default=2,
        help="The number of nodes to be tested",
    )
    # parser.add_argument(
    #     "--port",
    #     type=int,
    #     default=8080,
    #     help="The local port to listen on",
    # )
    parser.add_argument(
        "--running-time",
        default=600,
        type=int,
        help=(
            "How long should the instance run before the cluster is stopped "
            "(in seconds, default: %(default)s)"
        ),
    )
    now = datetime.now().strftime("%Y-%m-%d_%H.%M.%S")
    parser.set_defaults(log_file=f"net-measurements-tool-{now}.log")
    args = parser.parse_args()

    run_golem_example(
        main(
            subnet_tag=args.subnet_tag,
            payment_driver=args.payment_driver,
            payment_network=args.payment_network,
            num_instances=args.num_instances,
            running_time=args.running_time,
        ),
        log_file=args.log_file,
    )
