import json
from pathlib import Path
from typing import List

import sc2
from sc2.constants import *
from sc2.position import Point2
from sc2.unit import Unit


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        self.last_scout_time = 0
        self.scout_units = set()
        self.last_overseer_time = 0
        self.resource_list: List[List] = None
        self.expansion_locs = {}
        self.time_table = {}
        self.hq: Unit = None
        self.build_order = [
            SPAWNINGPOOL,
            # ROACHWARREN,
            HYDRALISKDEN,
            EVOLUTIONCHAMBER,
            # SPIRE,
        ]
        self.production_order = []

    def select_target(self):
        if self.known_enemy_structures.exists:
            return self.known_enemy_structures.closest_to(self.start_location).position
        return self.enemy_start_locations[0]

    async def on_step(self, iteration):
        larvae = self.units(LARVA)
        forces = self.units(ZERGLING).tags_not_in(self.scout_units) | \
                 self.units(HYDRALISK).tags_not_in(self.scout_units) | \
                 self.units(ROACH) | self.units(MUTALISK) | \
                 self.units(OVERSEER)

        self.production_order = []
        self.calc_resource_list()
        self.calc_expansion_loc()

        # supply_cap does not include overload that is being built
        if (self.units(OVERLORD).amount + self.already_pending(OVERLORD)) * 8 - self.supply_used < 2:
            if self.can_afford(OVERLORD) and larvae.exists:
                await self.do(larvae.random.train(OVERLORD))
                return

        if self.townhalls.amount <= 0:
            for unit in self.units(DRONE) | self.units(QUEEN) | forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            self.hq = self.townhalls.closest_to(self.start_location)

        for t in self.townhalls.ready:
            t: Unit = t

            excess_worker = self.workers.closer_than(10, t.position)
            m = self.need_worker_mineral()
            if t.assigned_harvesters > t.ideal_harvesters and excess_worker.exists and m is not None:
                await self.do(excess_worker.random.gather(m))

            if (self.units(SPAWNINGPOOL).ready.exists and
                    t.is_ready and t.noqueue and
                    not self.units(QUEEN).closer_than(10, t.position).exists):
                await self.do(t.train(QUEEN))
                self.production_order.append(QUEEN)

            if t.assigned_harvesters < t.ideal_harvesters:
                self.production_order.append(DRONE)

            queen_nearby = self.units(QUEEN).idle.closer_than(10, t.position)
            if queen_nearby.exists:
                queen = queen_nearby.first
                abilities = await self.get_available_abilities(queen)
                if AbilityId.EFFECT_INJECTLARVA in abilities:
                    await self.do(queen(EFFECT_INJECTLARVA, t))

        t = self.nearby_enemies()
        actions = []
        if t is not None:
            for unit in forces:
                actions.append(unit.attack(t.position))
        elif self.supply_used > 190:
            target = self.select_target()
            for unit in forces:
                unit: Unit = unit
                if not unit.is_attacking:
                    actions.append(unit.attack(target))
        else:
            far_h = self.townhalls.furthest_to(self.start_location)
            for unit in forces.further_than(10, far_h.position):
                if not unit.is_moving:
                    actions.append(unit.move(far_h.position.random_on_distance(5)))
        await self.do_actions(actions)

        if not self.units(LAIR).exists and self.already_pending(LAIR) == 0 and self.can_afford(LAIR):
            await self.do(self.hq.build(LAIR))

        # if not self.units(HIVE).exists and self.already_pending(HIVE) == 0 and self.can_afford(HIVE):
        #     await self.do(self.hq.build(HIVE))

        self.production_order.append(HYDRALISK)

        if self.units(ZERGLING).amount + self.already_pending(ZERGLING) < 7 or self.minerals - self.vespene > 500:
            self.production_order.append(ZERGLING)

        await self.call_every(self.scout_expansions, 3 * 60)
        await self.call_every(self.make_overseer, 20)
        await self.call_every(self.scout_watchtower, 60)

        enemy_nearby = self.known_enemy_units.closer_than(15, self.start_location)
        if enemy_nearby.amount > 5:
            if self.units(SPAWNINGPOOL).ready.exists:
                self.production_order = [ZERGLING]
            if self.units(HYDRALISKDEN).ready.exists:
                self.production_order = [HYDRALISK]
            for u in self.units:
                u: Unit = u
                u.attack(enemy_nearby.random)

        if self.should_expand() and self.resource_list is not None and len(self.resource_list) == 0:
            empty_expansions = set()
            for loc in self.expansion_locs:
                loc: Point2 = loc
                if self.townhalls.closer_than(self.EXPANSION_GAP_THRESHOLD, loc).amount == 0:
                    empty_expansions.add(loc)
            pos = self.start_location.closest(empty_expansions)
            await self.do(self.workers.random.build(HATCHERY, pos))

        if self.units(OVERLORD).amount == 1:
            o: Unit = self.units(OVERLORD).first
            await self.do_actions([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 20)),
                o.move(self.game_info.map_center, queue=True)
            ])

        if self.should_build_extractor():
            drone = self.workers.random
            target = self.state.vespene_geyser.closest_to(drone.position)
            await self.do(drone.build(EXTRACTOR, target))

        if self.supply_cap > 150 and self.already_pending_upgrade(OVERLORDSPEED) == 0:
            await self.do(self.hq.research(OVERLORDSPEED))

        for a in self.units(EXTRACTOR).ready:
            if a.assigned_harvesters < a.ideal_harvesters:
                w = self.workers.closer_than(20, a)
                if w.exists:
                    await self.do(w.random.gather(a))

        for d in self.units(DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        await self.build_building()
        await self.upgrade_building()
        await self.produce_unit()

    def economy_first(self):
        return self.townhalls.amount < 3 or self.units(QUEEN).amount < 3 or self.units(DRONE).amount < 44

    async def upgrade_building(self):
        if self.economy_first():
            return
        for b in self.build_order:
            u = self.units(b).ready
            if u.exists and u.first.is_idle:
                abilities = await self.get_available_abilities(u.first)
                if len(abilities) > 0:
                    await self.do(u.first(abilities[0]))

    async def build_building(self):
        if self.townhalls.amount < 2:
            return
        for i, b in enumerate(self.build_order):
            if ((i == 0 or self.units(self.build_order[i - 1]).exists) and
                    not self.units(b).exists and
                    self.already_pending(b) == 0 and
                    self.can_afford(b)):
                await self.build(b, near=self.hq.position.towards(self.game_info.map_center, 10))

    async def produce_unit(self):
        if QUEEN in self.production_order or self.should_expand():
            return
        for u in self.production_order:
            lv = self.units(LARVA)
            if lv.exists and self.can_afford(u):
                await self.do(lv.first.train(u))

    async def call_every(self, func, seconds):
        if func.__name__ not in self.time_table:
            self.time_table[func.__name__] = 0
        if self.time - self.time_table[func.__name__] > seconds:
            await func()

    async def scout_expansions(self):
        actions = []
        scouts = (self.units(ZERGLING).tags_not_in(self.scout_units) |
                  self.units(HYDRALISK).tags_not_in(self.scout_units))
        if scouts.exists:
            scout = scouts.random
            self.scout_units.add(scout.tag)
            locs = self.start_location.sort_by_distance(list(self.expansion_locs.keys()))
            for p in locs:
                actions.append(scout.move(p, queue=True))
            await self.do_actions(actions)
            self.time_table["scout_expansions"] = self.time

    async def scout_watchtower(self):
        if self.state.units(XELNAGATOWER).amount > 0:
            for x in self.state.units(XELNAGATOWER):
                x: Unit = x
                scouts = (self.units(ZERGLING).tags_not_in(self.scout_units) |
                          self.units(HYDRALISK).tags_not_in(self.scout_units))
                if not x.is_visible and scouts.exists:
                    scout = scouts.random
                    self.scout_units.add(scout.tag)
                    await self.do(scout.move(x.position))
                    self.time_table["scout_watchtower"] = self.time

    async def make_overseer(self):
        if self.units(LAIR).exists and self.units(OVERSEER).amount == 0 and self.can_afford(OVERSEER):
            await self.do(self.units(OVERLORD).random(MORPH_OVERSEER))
            self.time_table["make_overseer"] = self.time

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.already_pending(EXTRACTOR) > 0 or not self.units(SPAWNINGPOOL).exists:
            return False
        if self.townhalls.amount < 5:
            return self.units(EXTRACTOR).amount < self.townhalls.ready.amount * 2 - 2
        else:
            return self.units(EXTRACTOR).amount < self.townhalls.ready.amount * 2

    def nearby_enemies(self):
        for t in self.units.structure:
            t: Unit = t
            threats = self.known_enemy_units.closer_than(10, t.position)
            if threats.amount > 3:
                return threats.random
        return None

    def should_expand(self):
        if self.already_pending(HATCHERY) > 0:
            return False
        if not self.units(SPAWNINGPOOL).exists or not self.units(HYDRALISKDEN).exists:
            return self.townhalls.amount <= 1
        total_ideal_harvesters = 0
        for t in self.townhalls.ready:
            t: Unit = t
            total_ideal_harvesters += t.ideal_harvesters
        return total_ideal_harvesters < 16 * 4

    def calc_resource_list(self):
        if self.resource_list is not None:
            return
        RESOURCE_SPREAD_THRESHOLD = 144
        all_resources = self.state.mineral_field | self.state.vespene_geyser
        # Group nearby minerals together to form expansion locations
        r_groups = []
        for mf in all_resources:
            mf_height = self.get_terrain_height(mf.position)
            for g in r_groups:
                if any(
                        mf_height == self.get_terrain_height(p.position)
                        and mf.position._distance_squared(p.position) < RESOURCE_SPREAD_THRESHOLD
                        for p in g
                ):
                    g.append(mf)
                    break
            else:  # not found
                r_groups.append([mf])
        # Filter out bases with only one mineral field
        self.resource_list = [g for g in r_groups if len(g) > 1]

    def calc_expansion_loc(self):
        if not self.resource_list or len(self.resource_list) == 0:
            return
        # distance offsets from a gas geysir
        offsets = [(x, y) for x in range(-9, 10) for y in range(-9, 10) if 75 >= x ** 2 + y ** 2 >= 49]
        # for every resource group:
        resources = self.resource_list.pop()
        # possible expansion points
        # resources[-1] is a gas geysir which always has (x.5, y.5) coordinates, just like an expansion
        possible_points = (
            Point2((offset[0] + resources[-1].position.x, offset[1] + resources[-1].position.y))
            for offset in offsets
        )
        # filter out points that are too near
        possible_points = [
            point
            for point in possible_points
            if all(
                point.distance_to(resource) >= (6 if resource in self.state.mineral_field else 7)
                for resource in resources
            )
        ]
        # choose best fitting point
        result = min(possible_points, key=lambda p: sum(p.distance_to(resource) for resource in resources))
        self.expansion_locs[result] = resources
