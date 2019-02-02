import json

import sc2
from sc2 import run_game, maps, Race, Difficulty
from sc2.player import Bot, Computer
from bot import MyBot
from examples.zerg.zerg_rush import ZergRushBot


def main():
    sc2.paths.BASEDIR["Windows"] = "D:/Program Files (x86)/StarCraft II"

    with open("botinfo.json") as f:
        info = json.load(f)

    race = Race[info["race"]]
    run_game(maps.get("(2)DreamcatcherLE"), [
        Bot(race, MyBot()),
        # Bot(Race.Zerg, WorkerRushBot()),
        Computer(Race.Zerg, Difficulty.VeryHard),
    ], realtime=False, step_time_limit={"time_limit": 2, "window_size": 10, "penalty": 10}, game_time_limit=(60 * 30),
             save_replay_as="test.SC2Replay")


if __name__ == '__main__':
    main()
