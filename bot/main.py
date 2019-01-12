import json
import math
from pathlib import Path
from typing import List

import sc2
from sc2.constants import *
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        super().__init__()
        self.last_scout_time = 0
        self.scout_units = set()
        self.resource_list: List[List] = None
        self.expansion_locs = {}
        self.time_table = {}
        self.creep_queen_tag = 0
        self.hq: Unit = None
        self.all_in = False

        # enemy stats
        self.last_enemy_time = 0
        self.last_enemy_count = 0
        self.enemy_insight_frames = 0
        self.last_enemy_positions = []

        self.build_order = [
            UnitTypeId.SPAWNINGPOOL,
            # ROACHWARREN,
            UnitTypeId.HYDRALISKDEN,
            UnitTypeId.EVOLUTIONCHAMBER,
            # SPIRE,
        ]
        self.production_order = []

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
        buffer = 2
        if self.units(UnitTypeId.OVERLORD).amount <= 1:
            buffer = 1
        if (self.units(UnitTypeId.OVERLORD).amount + self.already_pending(
                UnitTypeId.OVERLORD)) * 8 + self.townhalls.ready.amount * 6 - self.supply_used < buffer:
            if self.can_afford(UnitTypeId.OVERLORD) and larvae.exists:
                await self.do(larvae.random.train(UnitTypeId.OVERLORD))
                return

        # enemy info
        self.calc_enemy_info()

        # counter timing attack
        if await self.defend_double_proxy_or_zergling_rush():
            return
        if await self.defend_cannon_rush():
            return

        # general defend strategy
        if self.last_enemy_positions and self.start_location.distance_to_closest(self.last_enemy_positions) < 10 and \
                (self.last_enemy_count >= 5 or self.enemy_insight_frames >= 10):
            if self.units(UnitTypeId.HYDRALISKDEN).ready.exists:
                await self.train(UnitTypeId.HYDRALISK)
            elif self.units(UnitTypeId.SPAWNINGPOOL).ready.exists:
                await self.train(UnitTypeId.ZERGLING)
            else:
                await self.train(UnitTypeId.DRONE)
            army = {UnitTypeId.DRONE, UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING}
            if self.units.of_type(
                    {UnitTypeId.ZERGLING, UnitTypeId.HYDRALISK, UnitTypeId.ROACH}).amount < self.last_enemy_count:
                army.add(UnitTypeId.DRONE)
            for u in self.units.of_type(army):
                u: Unit = u
                if not u.is_attacking:
                    await self.do(u.attack(u.position.closest(self.last_enemy_positions)))
            self.all_in = True
            return
        elif self.all_in:
            self.all_in = False
            for u in self.units.of_type({UnitTypeId.DRONE, UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING}):
                u: Unit = u
                await self.do(u.stop())

        # last resort
        if self.townhalls.amount <= 0:
            for unit in self.units(UnitTypeId.DRONE) | self.units(UnitTypeId.QUEEN) | forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            self.hq = self.townhalls.closest_to(self.start_location)

        # expansions
        for t in self.townhalls.ready:
            t: Unit = t

            excess_worker = self.workers.closer_than(10, t.position)
            m = self.need_worker_mineral()
            if t.assigned_harvesters > t.ideal_harvesters and excess_worker.exists and m is not None:
                await self.do(excess_worker.random.gather(m))

            if (t.assigned_harvesters < t.ideal_harvesters or self.townhalls.ready.amount == 1) and \
                    self.workers.amount + self.already_pending(UnitTypeId.DRONE) < 22 * 4:
                self.production_order.append(UnitTypeId.DRONE)

            queen_nearby = await self.inject_larva(t)

            if self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag) is None and queen_nearby.amount > 1:
                self.creep_queen_tag = queen_nearby[1].tag

        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                UnitTypeId.QUEEN not in self.production_order and \
                self.townhalls.amount >= 3 and \
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
                actions.append(unit.attack(target))
        else:
            far_h = self.townhalls.furthest_to(self.start_location)
            for unit in forces.further_than(10, far_h.position):
                if not unit.is_moving:
                    actions.append(unit.move(far_h.position.random_on_distance(5)))
        await self.do_actions(actions)

        self.production_order.append(UnitTypeId.HYDRALISK)

        zergling_amount = self.units(UnitTypeId.ZERGLING).amount + self.already_pending(UnitTypeId.ZERGLING)
        if zergling_amount < 9 or \
                self.minerals - self.vespene > 500 or \
                (self.already_pending_upgrade(UpgradeId.ZERGLINGATTACKSPEED) == 1 and zergling_amount < 40):
            self.production_order.insert(0, UnitTypeId.ZERGLING)

        if not self.units(UnitTypeId.LAIR).exists and \
                self.already_pending(UnitTypeId.LAIR, all_units=True) == 0 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                self.can_afford(UnitTypeId.LAIR):
            await self.do(self.hq.build(UnitTypeId.LAIR))

        if not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.HIVE, all_units=True) == 0 and \
                self.units(UnitTypeId.INFESTATIONPIT).ready.exists:
            self.production_order = []
            await self.do(self.hq.build(UnitTypeId.HIVE))

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
                        t) and not creep_tumors.closer_than(10, t).exists:
                    await self.do(creep_queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, t))

        actions = []
        for u in self.units(UnitTypeId.CREEPTUMORBURROWED):
            u: Unit = u
            abilities = await self.get_available_abilities(u)
            if AbilityId.BUILD_CREEPTUMOR_TUMOR in abilities:
                t = u.position.random_on_distance(10)
                if not creep_tumors.closer_than(10, t).exists and t.position.distance_to_closest(exp_points) > 5:
                    actions.append(u(AbilityId.BUILD_CREEPTUMOR_TUMOR, t))
        await self.do_actions(actions)

        if self.should_expand() and self.resource_list is not None and len(self.resource_list) == 0:
            empty_expansions = set()
            for loc in self.expansion_locs:
                loc: Point2 = loc
                if self.townhalls.closer_than(self.EXPANSION_GAP_THRESHOLD, loc).amount == 0:
                    empty_expansions.add(loc)
            pos = self.start_location.closest(empty_expansions)
            await self.build(UnitTypeId.HATCHERY, pos, max_distance=2, random_alternative=False, placement_step=1)

        if self.units(UnitTypeId.OVERLORD).amount == 1:
            o: Unit = self.units(UnitTypeId.OVERLORD).first
            await self.do_actions([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 20)),
                o.move(self.game_info.map_center, queue=True)
            ])

        o: Units = self.units(UnitTypeId.OVERLORD).idle
        if self.units(UnitTypeId.OVERLORD).amount == 2 and o.exists:
            await self.do(o.first.move(self.start_location.towards(self.game_info.map_center, 10), queue=True))

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

    async def inject_larva(self, townhall: Unit):
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and townhall.is_ready and townhall.noqueue:
            if self.units(UnitTypeId.QUEEN).closer_than(10, townhall.position).amount == 0:
                await self.do(townhall.train(UnitTypeId.QUEEN))
                self.production_order.append(UnitTypeId.QUEEN)
        queen_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(10, townhall.position)
        if queen_nearby.tags_not_in({self.creep_queen_tag}).amount > 0:
            queen = queen_nearby.tags_not_in({self.creep_queen_tag}).first
            abilities = await self.get_available_abilities(queen)
            if AbilityId.EFFECT_INJECTLARVA in abilities:
                await self.do(queen(AbilityId.EFFECT_INJECTLARVA, townhall))
        return queen_nearby

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
            if (i == 0 or self.units(self.build_order[i - 1]).exists) and self.should_build(b):
                await self.build(b, near=self.hq.position.random_on_distance(10))
        if self.should_build(UnitTypeId.INFESTATIONPIT) and self.supply_used > 150:
            self.production_order = []
            await self.build(UnitTypeId.INFESTATIONPIT, near=self.hq.position.random_on_distance(10))

    def should_build(self, b):
        return not self.units(b).exists and self.already_pending(b) == 0 and self.can_afford(b)

    def select_target(self):
        if self.known_enemy_structures.exists:
            return self.known_enemy_structures.furthest_to(self.enemy_start_locations[0]).position
        return self.enemy_start_locations[0]

    def calc_enemy_info(self):
        self.last_enemy_count = 0
        self.last_enemy_positions = []
        has_enemy = False
        for t in self.units.structure:
            t: Unit = t
            threats = self.known_enemy_units.exclude_type({
                UnitTypeId.OVERLORD,
                UnitTypeId.OVERSEER
            }).closer_than(10, t)
            self.last_enemy_count += threats.amount
            if threats.exists:
                self.last_enemy_positions.append(threats.closest_to(t).position)
                has_enemy = True

        if has_enemy:
            self.enemy_insight_frames += 1
            self.last_enemy_time = self.time
        else:
            self.enemy_insight_frames = 0

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

    def potential_scout_units(self):
        scouts = self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units)
        if not scouts.exists:
            scouts = self.units(UnitTypeId.HYDRALISK).tags_not_in(self.scout_units)
        return scouts

    async def scout_expansions(self):
        actions = []
        s = self.potential_scout_units()
        if s.exists:
            scout = s.random
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
                s = self.potential_scout_units()
                if not x.is_visible and s.exists:
                    scout = s.random
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
        if self.vespene - self.minerals > 100:
            return False
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
        if self.units(UnitTypeId.OVERLORD).amount == 1:
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

    async def defend_double_proxy_or_zergling_rush(self) -> bool:
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures.of_type({UnitTypeId.BARRACKS}).closer_than(half_size,
                                                                                                self.start_location)
        enemy_zerglings = self.known_enemy_units.of_type({UnitTypeId.ZERGLING}).closer_than(half_size,
                                                                                                self.start_location)
        if proxy_barracks.exists or enemy_zerglings.amount > self.units(UnitTypeId.ZERGLING).amount:
            townhall_to_defend = self.townhalls.ready.furthest_to(self.start_location)
            await self.do(self.townhalls.ready.closest_to(self.start_location)(AbilityId.RALLY_HATCHERY_UNITS,
                                                                               townhall_to_defend.position))
            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if self.units(UnitTypeId.HYDRALISKDEN).ready.exists:
                await self.train(UnitTypeId.HYDRALISK)
            elif sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if UpgradeId.ZERGLINGMOVEMENTSPEED in abilities:
                    await self.do(sp.first(UpgradeId.ZERGLINGMOVEMENTSPEED))
                await self.train(UnitTypeId.ZERGLING)
                if self.units(UnitTypeId.SPINECRAWLER).amount <= 2:
                    await self.build(UnitTypeId.SPINECRAWLER,
                                     near=townhall_to_defend.position.towards(self.game_info.map_center, 5),
                                     random_alternative=False)
            else:
                await self.train(UnitTypeId.DRONE)

            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.workers.closer_than(2.5, a):
                    await self.do(w.gather(self.state.mineral_field.closest_to(w)))

            forces = self.units.of_type({UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING})
            for d in self.units(UnitTypeId.DRONE).idle:
                await self.do(d.gather(self.state.mineral_field.closest_to(d)))

            if forces.idle.amount > 40 and self.time - self.last_enemy_time > 30:
                for f in forces.closer_than(half_size, self.start_location):
                    await self.do(f.attack(self.select_target()))

            if forces.idle.amount > 10:
                for f in forces.idle.random_group_of(
                        10 - min(forces.closer_than(half_size, self.enemy_start_locations[0]).amount, 10)):
                    f: Unit = f
                    await self.do(f.move(self.enemy_start_locations[0]))

            marines = self.known_enemy_units.of_type({UnitTypeId.MARINE})
            for t in self.townhalls.ready:
                t: Unit = t
                await self.inject_larva(t)
                if marines.exists and marines.closest_distance_to(t) < 10:
                    joint_forces = forces.idle | self.units(UnitTypeId.DRONE).closer_than(10, t.position)
                    for j in joint_forces:
                        j: Unit = j
                        if not j.is_attacking:
                            await self.do(j.attack(marines.random.position))
                    return True
            return True

        return False

    async def defend_cannon_rush(self):
        half_size = self.start_location.distance_to(self.game_info.map_center)
        cannons = self.known_enemy_structures.of_type({
            UnitTypeId.PYLON,
            UnitTypeId.PHOTONCANNON
        }).closer_than(20, self.start_location)
        if cannons.exists:
            # production queue
            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if self.units(UnitTypeId.HYDRALISKDEN).ready.exists:
                await self.train(UnitTypeId.HYDRALISK)
            elif sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if UpgradeId.ZERGLINGMOVEMENTSPEED in abilities:
                    await self.do(sp.first(UpgradeId.ZERGLINGMOVEMENTSPEED))
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(7, self.start_location).amount <= 1:
                    await self.build(
                        UnitTypeId.SPINECRAWLER,
                        self.state.mineral_field.closest_to(self.start_location).position,
                        random_alternative=False
                    )
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(3, sp.first.position).amount <= 0:
                    p: Point2 = sp.first.position.random_on_distance(3)
                    if not self.known_enemy_structures.of_type({UnitTypeId.PHOTONCANNON}).closer_than(7, p).exists:
                        await self.build(
                            UnitTypeId.SPINECRAWLER,
                            sp.first.position,
                            random_alternative=False
                        )

                await self.train(UnitTypeId.ZERGLING)
            # stop gathering gas
            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.workers.closer_than(2.5, a):
                    await self.do(w.gather(self.state.mineral_field.closest_to(w)))

            for d in self.units(UnitTypeId.DRONE).idle:
                await self.do(d.gather(self.state.mineral_field.closest_to(d)))

            forces = self.units.of_type({UnitTypeId.HYDRALISK, UnitTypeId.ZERGLING})

            if forces.idle.amount > 40:
                for f in forces.closer_than(half_size, self.start_location):
                    await self.do(f.attack(self.select_target()))

            if forces.idle.amount > 10:
                for f in forces.idle.random_group_of(
                        10 - min(forces.closer_than(half_size, self.enemy_start_locations[0]).amount, 10)):
                    f: Unit = f
                    await self.do(f.move(self.enemy_start_locations[0]))

            for t in self.townhalls.ready:
                t: Unit = t
                await self.inject_larva(t)
                lv = self.units(UnitTypeId.LARVA).closer_than(10, t.position)
                if t.surplus_harvesters < 0 and lv.exists:
                    await self.do(lv.first.train(UnitTypeId.DRONE))
            return True
        return False
