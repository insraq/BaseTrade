import json

from sc2 import run_game, maps, Race, Difficulty
from sc2.player import Bot, Computer

from bot import MyBot
from examples.protoss.cannon_rush import CannonRushBot


def main():
    with open("botinfo.json") as f:
        info = json.load(f)

    race = Race[info["race"]]

    run_game(maps.get("(2)DreamcatcherLE"), [
        Bot(race, MyBot()),
        Bot(Race.Protoss, CannonRushBot())
    ], realtime=False, step_time_limit=2.0, game_time_limit=(60*30), save_replay_as="test.SC2Replay")

if __name__ == '__main__':
    main()
