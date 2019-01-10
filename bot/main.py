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
        super().__init__()
        self.last_scout_time = 0
        self.scout_units = set()
        self.last_overseer_time = 0
        self.resource_list: List[List] = None
        self.expansion_locs = {}
        self.time_table = {}
        self.creep_queen_tag = 0
        self.used_creep_tumor = set()
        self.hq: Unit = None
        self.all_in = False
        self.build_order = [
            UnitTypeId.SPAWNINGPOOL,
            # ROACHWARREN,
            UnitTypeId.HYDRALISKDEN,
            UnitTypeId.EVOLUTIONCHAMBER,
            # SPIRE,
        ]
        self.production_order = []

    def select_target(self):
        if self.known_enemy_structures.exists:
            return self.known_enemy_structures.closest_to(self.start_location).position
        return self.enemy_start_locations[0]

    async def on_step(self, iteration):
        larvae = self.units(UnitTypeId.LARVA)
        forces = (self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units) |
                  self.units(UnitTypeId.HYDRALISK).tags_not_in(self.scout_units) |
                  self.units(UnitTypeId.ROACH) | self.units(UnitTypeId.MUTALISK) |
                  self.units(UnitTypeId.OVERSEER))

        self.production_order = []
        self.calc_resource_list()
        self.calc_expansion_loc()

        # supply_cap does not include overload that is being built
        if (self.units(UnitTypeId.OVERLORD).amount + self.already_pending(
                UnitTypeId.OVERLORD)) * 8 - self.supply_used < 2:
            if self.can_afford(UnitTypeId.OVERLORD) and larvae.exists:
                await self.do(larvae.random.train(UnitTypeId.OVERLORD))
                return

        # defend strategy
        enemy_units_nearby = self.known_enemy_units.exclude_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(10, self.start_location)
        enemy_workers_nearby = self.known_enemy_units.of_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(10, self.start_location)
        if (enemy_units_nearby.exists or enemy_workers_nearby.exists) and \
                self.units.of_type({UnitTypeId.ZERGLING, UnitTypeId.HYDRALISK,
                                    UnitTypeId.ROACH}).amount < enemy_units_nearby.amount + enemy_workers_nearby.amount:
            if self.units(UnitTypeId.HYDRALISKDEN).ready.exists:
                await self.train(UnitTypeId.HYDRALISK)
            elif self.units(UnitTypeId.SPAWNINGPOOL).ready.exists:
                await self.train(UnitTypeId.ZERGLING)
            for u in self.units.of_type({UnitTypeId.DRONE, UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING}):
                u: Unit = u
                if not u.is_attacking:
                    await self.do(u.attack((enemy_units_nearby | enemy_workers_nearby).random.position))
            self.all_in = True
            return
        elif self.all_in:
            self.all_in = False
            for u in self.units.of_type({UnitTypeId.DRONE, UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING}):
                u: Unit = u
                await self.do(u.stop())

        if self.townhalls.amount <= 0:
            for unit in self.units(UnitTypeId.DRONE) | self.units(UnitTypeId.QUEEN) | forces:
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

            if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and t.is_ready and t.noqueue:
                if self.units(UnitTypeId.QUEEN).closer_than(10, t.position).amount == 0:
                    await self.do(t.train(UnitTypeId.QUEEN))
                    self.production_order.append(UnitTypeId.QUEEN)

            if t.assigned_harvesters < t.ideal_harvesters and self.workers.amount + self.already_pending(
                    UnitTypeId.DRONE) < 22 * 4:
                self.production_order.append(UnitTypeId.DRONE)

            queen_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(10, t.position)
            if queen_nearby.tags_not_in({self.creep_queen_tag}).amount > 0:
                queen = queen_nearby.tags_not_in({self.creep_queen_tag}).first
                abilities = await self.get_available_abilities(queen)
                if AbilityId.EFFECT_INJECTLARVA in abilities:
                    await self.do(queen(AbilityId.EFFECT_INJECTLARVA, t))
            if self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag) is None and queen_nearby.amount > 1:
                self.creep_queen_tag = queen_nearby[1].tag

        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                UnitTypeId.QUEEN not in self.production_order and \
                self.units(UnitTypeId.QUEEN).amount + self.already_pending(UnitTypeId.QUEEN,
                                                                           all_units=True) <= self.townhalls.ready.amount:
            await self.do(self.townhalls.ready.furthest_to(self.start_location).train(UnitTypeId.QUEEN))
            self.production_order.append(UnitTypeId.QUEEN)

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

        if not self.units(UnitTypeId.LAIR).exists and \
                self.already_pending(UnitTypeId.LAIR) == 0 and \
                self.can_afford(UnitTypeId.LAIR):
            await self.do(self.hq.build(UnitTypeId.LAIR))

        # if not self.units(HIVE).exists and self.already_pending(HIVE) == 0 and self.can_afford(HIVE):
        #     await self.do(self.hq.build(HIVE))

        self.production_order.append(UnitTypeId.HYDRALISK)

        if self.units(UnitTypeId.ZERGLING).amount + self.already_pending(UnitTypeId.ZERGLING) < 7 or \
                self.minerals - self.vespene > 500:
            self.production_order.append(UnitTypeId.ZERGLING)

        await self.call_every(self.scout_expansions, 2 * 60)
        await self.call_every(self.make_overseer, 20)
        await self.call_every(self.scout_watchtower, 60)

        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        exp_points = list(self.expansion_locs.keys())
        creep_queen = self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag)
        if creep_queen is not None and creep_queen.is_idle:
            abilities = await self.get_available_abilities(creep_queen)
            if AbilityId.BUILD_CREEPTUMOR_QUEEN in abilities:
                t = self.townhalls.ready.furthest_to(self.start_location).position.random_on_distance(6)
                if creep_tumors.exists:
                    ct = creep_tumors.furthest_to(self.start_location).position.random_on_distance(10)
                    if ct.distance2_to(self.start_location) > t.distance2_to(self.start_location):
                        t = ct
                if t.position.distance_to_closest(exp_points) > 5 and self.has_creep(
                        t) and not creep_tumors.closer_than(8, t).exists:
                    await self.do(creep_queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, t))

        for u in self.units(UnitTypeId.CREEPTUMORBURROWED).tags_not_in(self.used_creep_tumor):
            u: Unit = u
            abilities = await self.get_available_abilities(u)
            if AbilityId.BUILD_CREEPTUMOR_TUMOR in abilities:
                t = u.position.random_on_distance(10)
                if not creep_tumors.closer_than(8, t).exists and t.position.distance_to_closest(exp_points) > 5:
                    err = await self.do(u(AbilityId.BUILD_CREEPTUMOR_TUMOR, t))
                    if not err:
                        self.used_creep_tumor.add(u.tag)

        if self.should_expand() and self.resource_list is not None and len(self.resource_list) == 0:
            empty_expansions = set()
            for loc in self.expansion_locs:
                loc: Point2 = loc
                if self.townhalls.closer_than(self.EXPANSION_GAP_THRESHOLD, loc).amount == 0:
                    empty_expansions.add(loc)
            pos = self.start_location.closest(empty_expansions)
            await self.do(self.workers.random.build(UnitTypeId.HATCHERY, pos))

        if self.units(UnitTypeId.OVERLORD).amount == 1:
            o: Unit = self.units(UnitTypeId.OVERLORD).first
            await self.do_actions([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 20)),
                o.move(self.game_info.map_center, queue=True)
            ])

        if self.should_build_extractor():
            drone = self.workers.random
            target = self.state.vespene_geyser.closest_to(drone.position)
            await self.do(drone.build(UnitTypeId.EXTRACTOR, target))

        if self.supply_cap > 150 and self.already_pending_upgrade(UpgradeId.OVERLORDSPEED) == 0:
            self.production_order = []
            await self.do(self.hq.research(UpgradeId.OVERLORDSPEED))

        for a in self.units(UnitTypeId.EXTRACTOR).ready:
            if a.assigned_harvesters < a.ideal_harvesters:
                w = self.workers.closer_than(20, a)
                if w.exists:
                    await self.do(w.random.gather(a))

        for d in self.units(UnitTypeId.DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        await self.build_building()
        await self.upgrade_building()
        await self.produce_unit()

    def economy_first(self):
        return self.townhalls.amount < 3 or \
               self.units(UnitTypeId.QUEEN).amount < 3 or \
               self.units(UnitTypeId.DRONE).amount < 44

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
                await self.build(b, near=self.hq.position.random_on_distance(10))

    async def produce_unit(self):
        if UnitTypeId.QUEEN in self.production_order or self.should_expand() or self.supply_left == 0:
            return
        for u in self.production_order:
            await self.train(u)

    async def train(self, u):
        lv = self.units(UnitTypeId.LARVA)
        if lv.exists and self.can_afford(u):
            await self.do(lv.random.train(u))

    async def call_every(self, func, seconds):
        if func.__name__ not in self.time_table:
            self.time_table[func.__name__] = 0
        if self.time - self.time_table[func.__name__] > seconds:
            await func()

    async def scout_expansions(self):
        actions = []
        scouts = (self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units) |
                  self.units(UnitTypeId.HYDRALISK).tags_not_in(self.scout_units))
        if scouts.exists:
            scout = scouts.random
            self.scout_units.add(scout.tag)
            locs = self.start_location.sort_by_distance(list(self.expansion_locs.keys()))
            for p in locs:
                if not self.is_visible(p):
                    actions.append(scout.move(p, queue=True))
            await self.do_actions(actions)
            self.time_table["scout_expansions"] = self.time

    async def scout_watchtower(self):
        if self.state.units(UnitTypeId.XELNAGATOWER).amount > 0:
            for x in self.state.units(UnitTypeId.XELNAGATOWER):
                x: Unit = x
                scouts = (self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units) |
                          self.units(UnitTypeId.HYDRALISK).tags_not_in(self.scout_units))
                if not x.is_visible and scouts.exists:
                    scout = scouts.random
                    self.scout_units.add(scout.tag)
                    await self.do(scout.move(x.position))
                    self.time_table["scout_watchtower"] = self.time

    async def make_overseer(self):
        if self.units(UnitTypeId.LAIR).exists and \
                self.units(UnitTypeId.OVERSEER).amount == 0 and \
                self.can_afford(UnitTypeId.OVERSEER):
            await self.do(self.units(UnitTypeId.OVERLORD).random(AbilityId.MORPH_OVERSEER))
            self.time_table["make_overseer"] = self.time

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.already_pending(UnitTypeId.EXTRACTOR) > 0 or not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if self.townhalls.amount < 5:
            return self.units(UnitTypeId.EXTRACTOR).amount < self.townhalls.ready.amount * 2 - 2
        else:
            return self.units(UnitTypeId.EXTRACTOR).amount < self.townhalls.ready.amount * 2

    def nearby_enemies(self):
        for t in self.units.structure:
            t: Unit = t
            threats = self.known_enemy_units \
                .exclude_type({UnitTypeId.OVERLORD, UnitTypeId.OVERSEER, UnitTypeId.DRONE,
                               UnitTypeId.SCV, UnitTypeId.PROBE}) \
                .closer_than(10, t.position)
            if threats.exists:
                return threats.random
        return None

    def should_expand(self):
        if self.already_pending(UnitTypeId.HATCHERY) > 0:
            return False
        if not (self.already_pending(UnitTypeId.LAIR, all_units=True) > 0 or self.units(UnitTypeId.LAIR).exists):
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
