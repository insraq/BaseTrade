import json
import math
from pathlib import Path
from typing import List, Dict, Set, Union

import sc2
from sc2 import Race
from sc2.cache import property_cache_once_per_frame
from sc2.constants import *
from sc2.position import Point2, Rect
from sc2.unit import Unit
from sc2.units import Units


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def __init__(self):
        super().__init__()
        self.last_scout_time = 0
        self.scout_units = set()
        self.base_trade_units = set()
        self.resource_list: List[List] = None
        self.time_table = {}
        self.units_health: Dict[int, Union[int, float]] = {}
        self.units_attacked: Units = None
        self.units_attacked_tags: List[Set[int]] = []
        self.creep_queen_tag = 0
        self.far_corners: Set[Point2] = set()
        self.hq: Unit = None
        self.all_in = False
        self.enemy_unit_history: Dict[UnitTypeId, Set[int]] = {}
        self.first_overlord_tag = 0
        self.iteration = 0
        self.last_attack_iter = 0

        # enemy stats
        self.enemy_expansions: Units = None

        self.build_order = []
        self.production_order = []
        self.forces: Units = None

    def _prepare_first_step(self):
        sc2.BotAI._prepare_first_step(self)
        self.expansion_locations.keys()
        a: Rect = self.game_info.playable_area
        corners = {Point2((a.x, a.y)), Point2((a.x, a.height)), Point2((a.width, a.y)), Point2((a.width, a.height))}
        s = self.start_location.closest(corners)
        e = self.enemy_start_locations[0].closest(corners)
        self.far_corners = corners - {s, e}

    async def on_step(self, iteration):

        if self.time_budget_available and self.time_budget_available < 0.05:
            return

        self.production_order = []
        # enemy info
        self.calc_enemy_info()
        self.iteration = iteration

        self.forces = (self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units | self.base_trade_units) |
                       self.units(UnitTypeId.BANELING) |
                       self.units(UnitTypeId.HYDRALISK) |
                       self.units(UnitTypeId.ROACH).tags_not_in(self.scout_units | self.base_trade_units) |
                       self.units(UnitTypeId.MUTALISK) |
                       self.units(UnitTypeId.OVERSEER))
        half_size = self.start_location.distance_to(self.game_info.map_center)

        # if i don't even have a townhall
        # this has to be there because sometimes `self.townhalls` returns nothing even though there're clearly townhalls
        if not self.townhalls.exists:
            for unit in self.units(UnitTypeId.DRONE) | self.units(UnitTypeId.QUEEN) | self.forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            self.hq = self.townhalls.closest_to(self.start_location)
            far_townhall: Unit = self.townhalls.closest_to(self.game_info.map_center)
            rally_point: Point2 = far_townhall.position.towards(self.game_info.map_center, 4)

        is_terran = self.enemy_race == Race.Terran or \
                    (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Terran)

        is_zerg = self.enemy_race == Race.Zerg or \
                  (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Zerg)

        is_protoss = self.enemy_race == Race.Protoss or \
                     (self.known_enemy_units.exists and self.known_enemy_units.first.race == Race.Protoss)

        if is_terran:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.BANELINGNEST,
                UnitTypeId.HYDRALISKDEN,
                UnitTypeId.EVOLUTIONCHAMBER,
            ]
        else:
            self.build_order = [
                UnitTypeId.SPAWNINGPOOL,
                UnitTypeId.ROACHWARREN,
                UnitTypeId.HYDRALISKDEN,
                UnitTypeId.EVOLUTIONCHAMBER,
            ]

        # supply_cap does not include overload that is being built
        est_supply_cap = (self.count_unit(UnitTypeId.OVERLORD)) * 8 + self.townhalls.ready.amount * 6
        est_supply_left = est_supply_cap - self.supply_used
        if self.units(UnitTypeId.OVERLORD).amount == 1 and self.townhalls.amount < 2:
            build_overlord = False
        elif self.units(UnitTypeId.OVERLORD).amount <= 4:
            build_overlord = est_supply_left < 3
        else:
            build_overlord = est_supply_left < 8

        if build_overlord and est_supply_cap <= 200:
            await self.train(UnitTypeId.OVERLORD)

        # attacks
        actions = []
        enemy_nearby = self.enemy_nearby()
        if enemy_nearby:
            for unit in self.forces:
                unit: Unit = unit
                # fight within spinecrawler
                sc = self.units(UnitTypeId.SPINECRAWLER).ready
                if not sc.exists or \
                        sc.closest_distance_to(rally_point) > 15 or \
                        sc.closest_distance_to(unit.position) > 15:
                    actions.append(unit.attack(enemy_nearby.position))
                    continue
                if sc.filter(lambda u: u.is_attacking).exists:
                    actions.append(unit.attack(enemy_nearby.position))
                else:
                    actions.append(unit.move(self.start_location))
            for unit in self.units(UnitTypeId.SWARMHOSTMP).ready:
                abilities = (await self.get_available_abilities([unit]))[0]
                if AbilityId.EFFECT_SPAWNLOCUSTS in abilities and enemy_nearby.position.distance_to(
                        unit.position) < 20:
                    actions.append(unit(AbilityId.EFFECT_SPAWNLOCUSTS, enemy_nearby.position))
        elif self.supply_used > 190:
            if iteration - self.last_attack_iter > 10:
                self.last_attack_iter = iteration
                for unit in self.forces:
                    if not unit.is_attacking and unit.health_percentage >= 0.1:
                        actions.append(unit.attack(self.attack_target))
        else:
            for unit in self.forces.further_than(10, rally_point):
                if unit.type_id == UnitTypeId.BANELING and unit.is_attacking:
                    continue
                if not unit.is_moving:
                    actions.append(unit.move(rally_point))
        swarmhost = self.units(UnitTypeId.SWARMHOSTMP).ready
        sa = []
        if swarmhost.amount >= 5:
            for s in swarmhost:
                s: Unit = s
                abilities = (await self.get_available_abilities([s]))[0]
                if self.enemy_expansions.exists and AbilityId.EFFECT_SPAWNLOCUSTS in abilities:
                    closest_exp = self.enemy_expansions.closest_to(s.position)
                    sa.append(s.move(closest_exp.position.towards(self.start_location, 20), queue=True))
                    sa.append(s(AbilityId.EFFECT_SPAWNLOCUSTS, closest_exp.position, queue=True))
                    sa.append(s.move(rally_point, queue=True))
        if len(sa) >= 15:
            actions.extend(sa)
        # attack reactions
        for x in self.units_attacked:
            x: Unit = x
            workers_nearby = self.workers.closer_than(5, x.position).filter(lambda wk: not wk.is_attacking)
            enemy_nearby = self.alive_enemy_units.closer_than(5, x.position)
            if not enemy_nearby.exists:
                continue
            if x.type_id == UnitTypeId.DRONE:
                another_townhall = self.townhalls.further_than(25, x.position)
                if self.forces.amount > enemy_nearby.amount and another_townhall.exists and self.townhalls.ready.amount > 3:
                    actions.append(x.move(another_townhall.first.position))
                elif workers_nearby.amount > 2:
                    actions.append(x.attack(enemy_nearby.first))
                    for w in workers_nearby:
                        w: Unit = w
                        if not w.is_attacking:
                            actions.append(w.attack(enemy_nearby.first))
            elif x.is_structure:
                if x.build_progress < 1 and x.health_percentage < 0.1:
                    actions.append(x(AbilityId.CANCEL))
            elif x.type_id == UnitTypeId.SWARMHOSTMP:
                actions.append(x.move(rally_point))
            elif x.tag == self.first_overlord_tag:
                actions.append(x.move(self.game_info.map_center))
            elif x.health_percentage < 0.1:
                actions.append(x.move(rally_point))
            if not x.is_idle:
                continue
            if self.forces.closer_than(10, x.position).amount > self.alive_enemy_units.closer_than(10,
                                                                                                   x.position).amount:
                actions.append(x.attack(enemy_nearby.first, queue=True))
            else:
                actions.append(x.move(rally_point))
        await self.do_actions(actions)

        # counter timing attack
        if await self.defend_early_rush():
            return
        if await self.defend_cannon_rush():
            return

        # build spinecrawlers
        number_to_build = 2 if is_zerg else 1
        if self.count_unit(UnitTypeId.SPINECRAWLER) < number_to_build and \
                self.townhalls.ready.amount > 1 and \
                self.can_afford_or_change_production(UnitTypeId.SPINECRAWLER):
            await self.build_spine_crawler()

        # economy
        for t in self.townhalls.ready:
            t: Unit = t
            queen_nearby = await self.dist_workers_and_inject_larva(t)
            if self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag) is None and queen_nearby.amount > 1:
                self.creep_queen_tag = queen_nearby[1].tag
            if self.townhalls.amount >= 3:
                if not self.units(UnitTypeId.SPORECRAWLER).closer_than(10, t.position).exists and \
                        self.already_pending(UnitTypeId.SPORECRAWLER) == 0:
                    await self.build(UnitTypeId.SPORECRAWLER,
                                     near=t.position.towards(self.state.mineral_field.closest_to(t).position, 3),
                                     random_alternative=False)

        if (self.count_unit(UnitTypeId.DRONE) < self.townhalls.amount * 16 + self.units(
                UnitTypeId.EXTRACTOR).amount * 3 or self.townhalls.ready.amount == 1) \
                and self.count_unit(UnitTypeId.DRONE) < 76:
            if self.forces.amount >= self.alive_enemy_units.amount or self.townhalls.amount < 2:
                self.production_order.append(UnitTypeId.DRONE)
            else:
                self.production_order.extend([UnitTypeId.HYDRALISK, UnitTypeId.ROACH, UnitTypeId.ZERGLING])

        # production queue
        # roach and hydra
        if self.units(UnitTypeId.ROACHWARREN).ready.exists and not self.units(
                UnitTypeId.HYDRALISKDEN).ready.exists and self.count_unit(UnitTypeId.ROACH) < 10:
            self.production_order.append(UnitTypeId.ROACH)
        else:
            self.production_order.extend([UnitTypeId.HYDRALISK])
        # swarm host
        if self.units(UnitTypeId.INFESTATIONPIT).ready.exists and self.count_unit(UnitTypeId.SWARMHOSTMP) < 10:
            if self.supply_used > 150:
                self.production_order = [UnitTypeId.SWARMHOSTMP]
            else:
                self.production_order.append(UnitTypeId.SWARMHOSTMP)
        # zerglings
        zergling_amount = self.units(UnitTypeId.ZERGLING).amount + 2 * self.already_pending(UnitTypeId.ZERGLING)
        if self.townhalls.ready.amount == 1 and zergling_amount < 6 + self.state.units(UnitTypeId.XELNAGATOWER).amount:
            self.production_order.insert(0, UnitTypeId.ZERGLING)
        elif zergling_amount < self.townhalls.amount * 6:
            self.production_order.append(UnitTypeId.ZERGLING)
        if self.already_pending_upgrade(UpgradeId.ZERGLINGATTACKSPEED) == 1 and zergling_amount < 30:
            self.production_order.append(UnitTypeId.ZERGLING)
        if UnitTypeId.BANELINGNEST in self.build_order and (zergling_amount < self.townhalls.ready.amount * 10):
            self.production_order.append(UnitTypeId.ZERGLING)
        # banelings
        if self.units(UnitTypeId.BANELINGNEST).ready.exists and self.units(UnitTypeId.ZERGLING).exists:
            if (self.townhalls.ready.amount == 2 and self.count_unit(UnitTypeId.BANELING) < 5) or \
                    (self.townhalls.ready.amount == 3 and self.count_unit(UnitTypeId.BANELING) < 10) or \
                    (self.townhalls.ready.amount >= 4 and self.count_unit(UnitTypeId.BANELING) < 20):
                z = self.units(UnitTypeId.ZERGLING).closer_than(half_size, self.start_location)
                if z.exists:
                    await self.do(z.first(AbilityId.MORPHZERGLINGTOBANELING_BANELING))

        # lair upgrade
        if not self.units(UnitTypeId.LAIR).exists and \
                not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.LAIR, all_units=True) == 0 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                (self.count_unit(UnitTypeId.ROACH) > 0 and UnitTypeId.ROACHWARREN in self.build_order or
                 self.townhalls.amount >= 3 and UnitTypeId.BANELINGNEST in self.build_order) and \
                self.can_afford_or_change_production(UnitTypeId.LAIR):
            await self.do(self.hq.build(UnitTypeId.LAIR))

        # hive upgrade
        if not self.units(UnitTypeId.HIVE).exists and \
                self.already_pending(UnitTypeId.HIVE, all_units=True) == 0 and \
                self.units(UnitTypeId.INFESTATIONPIT).ready.exists and \
                self.can_afford_or_change_production(UnitTypeId.HIVE):
            await self.do(self.hq.build(UnitTypeId.HIVE))

        if self.enemy_expansions.exists:
            await self.call_every(self.scout_expansions, 2 * 60)
        else:
            await self.call_every(self.scout_expansions, 60)
        await self.call_every(self.scout_watchtower, 60)
        await self.fill_creep_tumor()
        await self.make_overseer()

        # expansion
        if self.should_expand() and self.can_afford_or_change_production(UnitTypeId.HATCHERY):
            await self.expand_now(None, 2)

        # first overlord scout
        if self.units(UnitTypeId.OVERLORD).amount == 1:
            o: Unit = self.units(UnitTypeId.OVERLORD).first
            self.first_overlord_tag = o.tag
            exps = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            await self.do_actions([
                o.move(self.enemy_start_locations[0].towards(self.game_info.map_center, 18)),
                o.move(exps[1].towards(self.game_info.map_center, 10), queue=True),
            ])

        # second overlord scout
        o: Units = self.units(UnitTypeId.OVERLORD).idle
        if self.units(UnitTypeId.OVERLORD).amount == 2 and o.exists:
            await self.do(o.first.move(self.start_location.towards(self.game_info.map_center, 10), queue=True))

        # extractor and gas gathering
        if self.should_build_extractor():
            drone = self.empty_workers.random
            target = self.state.vespene_geyser.filter(lambda u: not u.is_mine).closest_to(drone.position)
            if self.townhalls.ready.closest_distance_to(target.position) < 10:
                await self.do(drone.build(UnitTypeId.EXTRACTOR, target))

        actions = []
        for a in self.units(UnitTypeId.EXTRACTOR).ready:
            a: Unit = a
            if self.vespene - self.minerals > 200:
                w: Units = self.empty_workers.closer_than(2.5, a)
                t: Unit = self.townhalls.closest_to(a.position)
                if t.surplus_harvesters < 0 and t.distance_to(a) < 10 and w.exists and w.first.order_target == a.tag:
                    actions.append(w.first.gather(self.state.mineral_field.closest_to(w.first)))
            elif a.assigned_harvesters < a.ideal_harvesters:
                w: Units = self.empty_workers.closer_than(20, a)
                if w.exists:
                    actions.append(w.random.gather(a))
                    continue
            elif a.assigned_harvesters > a.ideal_harvesters:
                for w in self.empty_workers.closer_than(2.5, a):
                    if w.order_target == a.tag:
                        actions.append(w.gather(self.state.mineral_field.closest_to(w)))
        await self.do_actions(actions)

        # overlord speed
        if self.units(UnitTypeId.LAIR).ready.exists and \
                self.already_pending_upgrade(UpgradeId.OVERLORDSPEED) == 0 and \
                self.can_afford_or_change_production(UpgradeId.OVERLORDSPEED):
            await self.do(self.hq.research(UpgradeId.OVERLORDSPEED))

        # drone
        for d in self.units(UnitTypeId.DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        await self.build_building()
        await self.upgrade_building()
        await self.produce_unit()

    async def fill_creep_tumor(self):
        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        creep_queen = self.units(UnitTypeId.QUEEN).find_by_tag(self.creep_queen_tag)
        # make creep queen
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                UnitTypeId.QUEEN not in self.production_order and \
                self.townhalls.amount >= 3 and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN) and \
                self.count_unit(UnitTypeId.QUEEN) <= self.townhalls.ready.amount:
            await self.do(self.townhalls.ready.furthest_to(self.start_location).train(UnitTypeId.QUEEN))
        if creep_queen is not None and creep_queen.is_idle:
            abilities = await self.get_available_abilities(creep_queen)
            if AbilityId.BUILD_CREEPTUMOR_QUEEN in abilities:
                t = self.townhalls.ready.furthest_to(self.start_location).position.random_on_distance(6)
                if creep_tumors.exists:
                    ct = creep_tumors.furthest_to(self.start_location).position.random_on_distance(10)
                    if ct.distance2_to(self.start_location) > t.distance2_to(self.start_location):
                        t = ct
                if self.has_creep(t) and self.can_place_creep_tumor(t):
                    await self.do(creep_queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, t))
        actions = []
        available_creep_tumors = self.units(UnitTypeId.CREEPTUMORBURROWED)
        if available_creep_tumors.exists:
            abilities: List[List[AbilityId]] = await self.get_available_abilities(available_creep_tumors)
            for i, a in enumerate(abilities):
                if AbilityId.BUILD_CREEPTUMOR_TUMOR in a:
                    u: Unit = available_creep_tumors[i]
                    t = self.calc_creep_tumor_position(u)
                    if t is not None:
                        actions.append(u(AbilityId.BUILD_CREEPTUMOR_TUMOR, t))
        await self.do_actions(actions)

    async def dist_workers_and_inject_larva(self, townhall: Unit) -> Units:
        excess_worker = self.empty_workers.closer_than(10, townhall.position)
        m = self.need_worker_mineral()
        if townhall.assigned_harvesters > townhall.ideal_harvesters and excess_worker.exists and m is not None:
            await self.do(excess_worker.random.gather(m))
        if self.units(UnitTypeId.SPAWNINGPOOL).ready.exists and \
                townhall.is_ready and townhall.noqueue and \
                self.units(UnitTypeId.QUEEN).closer_than(10, townhall.position).amount == 0 and \
                self.can_afford_or_change_production(UnitTypeId.QUEEN):
            await self.do(townhall.train(UnitTypeId.QUEEN))
        queen_nearby = self.units(UnitTypeId.QUEEN).idle.closer_than(10, townhall.position)
        if queen_nearby.tags_not_in({self.creep_queen_tag}).amount > 0:
            queen = queen_nearby.tags_not_in({self.creep_queen_tag}).first
            abilities = await self.get_available_abilities(queen)
            if AbilityId.EFFECT_INJECTLARVA in abilities:
                await self.do(queen(AbilityId.EFFECT_INJECTLARVA, townhall))
        return queen_nearby

    def calc_creep_tumor_position(self, u: Unit):
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi / 4)
            if self.can_place_creep_tumor(t):
                return t
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi / 2)
            if self.can_place_creep_tumor(t):
                return t
        for i in range(0, 5):
            t = u.position.towards_with_random_angle(self.enemy_start_locations[0], 10, math.pi)
            if self.can_place_creep_tumor(t):
                return t
        return None

    @property_cache_once_per_frame
    def empty_workers(self) -> Units:
        def has_no_resource(u: Unit):
            return not u.is_carrying_minerals and not u.is_carrying_vespene

        return self.workers.filter(has_no_resource)

    def can_place_creep_tumor(self, t: Point2) -> bool:
        creep_tumors = self.units.of_type({
            UnitTypeId.CREEPTUMOR,
            UnitTypeId.CREEPTUMORBURROWED,
            UnitTypeId.CREEPTUMORMISSILE,
            UnitTypeId.CREEPTUMORQUEEN,
        })
        exp_points = list(self.expansion_locations.keys())
        return not creep_tumors.closer_than(10, t).exists and t.position.distance_to_closest(exp_points) > 5

    async def upgrade_building(self):
        if self.workers.collecting.amount < 32:
            return
        if self.forces.amount < self.alive_enemy_units.amount:
            return
        for b in self.build_order:
            if b == UnitTypeId.ROACHWARREN:
                return
            u = self.units(b).ready
            if u.exists and u.first.is_idle:
                abilities = await self.get_available_abilities(u.first, ignore_resource_requirements=True)
                if len(abilities) > 0 and self.can_afford_or_change_production(abilities[0]):
                    await self.do(u.first(abilities[0]))

    async def build_building(self):
        for i, b in enumerate(self.build_order):
            for t in self.townhalls.sorted_by_distance_to(self.start_location):
                t: Unit = t
                p = t.position.random_on_distance(10)
                if (i == 0 or self.units(self.build_order[i - 1]).exists) and self.should_build(
                        b) and self.is_location_safe(p):
                    if b == UnitTypeId.ROACHWARREN and self.workers.amount < 16 * 2:
                        return
                    if b == UnitTypeId.BANELINGNEST and self.workers.amount < 16 * 2:
                        return
                    await self.build(b, near=p)
                    return
        if self.should_build(UnitTypeId.INFESTATIONPIT) and \
                self.count_unit(UnitTypeId.HYDRALISK) >= 10 and \
                self.units(UnitTypeId.LAIR).ready.exists and \
                self.can_afford_or_change_production(UnitTypeId.INFESTATIONPIT):
            await self.build(UnitTypeId.INFESTATIONPIT, near=self.hq.position.random_on_distance(10))

        if self.count_unit(UnitTypeId.EVOLUTIONCHAMBER) == 1 and self.supply_used > 100:
            await self.build(UnitTypeId.EVOLUTIONCHAMBER, near=self.hq.position.random_on_distance(10))

    def should_build(self, b):
        return not self.units(b).exists and self.already_pending(b) == 0 and self.can_afford(b)

    def count_unit(self, unit_type: UnitTypeId) -> int:
        return self.units(unit_type).amount + self.already_pending(unit_type, all_units=True)

    @property_cache_once_per_frame
    def attack_target(self):
        if self.known_enemy_structures.exists:
            target = self.known_enemy_structures.furthest_to(self.enemy_start_locations[0])
            return target.position
        return self.enemy_start_locations[0]

    def enemy_nearby(self):
        for t in self.townhalls.ready:
            t: Unit = t
            e: Units = self.alive_enemy_units.closer_than(20, t.position)
            if e.visible.amount > 0:
                return e.closest_to(t.position)
        return None

    @property_cache_once_per_frame
    def alive_enemy_units(self) -> Units:
        def alive_and_can_attack(u: Unit) -> bool:
            return u.health > 0 and u.can_attack

        return self.known_enemy_units.filter(alive_and_can_attack)

    def calc_enemy_info(self):
        self.enemy_expansions = self.known_enemy_structures.of_type({
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.PLANETARYFORTRESS
        })
        for e in self.known_enemy_units:
            e: Unit = e
            if e.type_id not in self.enemy_unit_history:
                self.enemy_unit_history[e.type_id] = set()
            self.enemy_unit_history[e.type_id].add(e.tag)

        def not_full_health(u: Unit) -> bool:
            return u.health < u.health_max

        units_attacked = set()
        for w in self.units.filter(not_full_health):
            w: Unit = w
            if (w.tag in self.units_health and w.health > self.units_health[w.tag]) or w.tag not in self.units_health:
                units_attacked.add(w.tag)
            self.units_health[w.tag] = w.health

        if len(self.units_attacked_tags) >= 5:
            self.units_attacked_tags.pop(0)
        self.units_attacked_tags.append(units_attacked)

        tags_union: Set[int] = set()
        for s in self.units_attacked_tags:
            tags_union = tags_union | s
        self.units_attacked = self.units.tags_in(tags_union)

    async def produce_unit(self):
        if self.supply_left == 0:
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

    def can_afford_or_change_production(self, u):

        def remove_if_exists(l, i):
            if i in l:
                l.remove(i)

        can_afford = self.can_afford(u)
        if not can_afford.can_afford_minerals:
            self.production_order = []
        if not can_afford.can_afford_vespene:
            remove_if_exists(self.production_order, UnitTypeId.ROACH)
            remove_if_exists(self.production_order, UnitTypeId.HYDRALISK)
            remove_if_exists(self.production_order, UnitTypeId.MUTALISK)
            remove_if_exists(self.production_order, UnitTypeId.SWARMHOSTMP)
        return can_afford

    def potential_scout_units(self):
        if self.supply_used > 190 and self.already_pending(UpgradeId.OVERLORDSPEED) == 1:
            scouts = self.units(UnitTypeId.OVERLORD).tags_not_in(self.scout_units)
        else:
            scouts = self.units(UnitTypeId.ZERGLING).tags_not_in(self.scout_units)
        if not scouts.exists:
            scouts = self.units(UnitTypeId.OVERLORD)
        return scouts

    async def scout_expansions(self):
        if self.townhalls.amount <= 2 and (
                not self.units(UnitTypeId.SPINECRAWLER).exists or
                self.units(UnitTypeId.SPINECRAWLER).first.build_progress < 0.5
        ):
            return
        actions = []
        s = self.potential_scout_units()
        if s.exists:
            scout = s.random
            self.scout_units.add(scout.tag)
            locs = self.enemy_start_locations[0].sort_by_distance(list(self.expansion_locations.keys()))
            locs.reverse()
            for i, p in enumerate(locs):
                if not self.is_visible(p):
                    actions.append(scout.move(p, queue=i > 0))
            await self.do_actions(actions)
            self.time_table["scout_expansions"] = self.time

    async def scout_watchtower(self):
        if self.townhalls.amount <= 2 and not self.units(UnitTypeId.SPINECRAWLER).ready.exists:
            return
        if self.state.units(UnitTypeId.XELNAGATOWER).amount > 0:
            for x in self.state.units(UnitTypeId.XELNAGATOWER):
                x: Unit = x
                s = self.potential_scout_units()
                scouts = self.units.of_type({UnitTypeId.ZERGLING, UnitTypeId.OVERLORD})
                if scouts.exists and scouts.closest_distance_to(x.position) > 2 and s.exists:
                    scout = s.random
                    self.scout_units.add(scout.tag)
                    await self.do_actions([
                        scout.move(x.position),
                        scout.hold_position(queue=True)
                    ])
                    self.time_table["scout_watchtower"] = self.time

    async def make_overseer(self):
        if self.units(UnitTypeId.LAIR).exists and \
                self.count_unit(UnitTypeId.OVERSEER) == 0 and \
                self.can_afford_or_change_production(UnitTypeId.OVERSEER):
            await self.do(self.units(UnitTypeId.OVERLORD).random(AbilityId.MORPH_OVERSEER))

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_build_extractor(self):
        if self.vespene - self.minerals > 100:
            return False
        if not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if self.minerals - self.vespene > 500:
            return True
        if self.townhalls.ready.amount < 2:
            return False
        if self.townhalls.amount < 3:
            return self.count_unit(UnitTypeId.EXTRACTOR) == 0
        if self.townhalls.amount < 4:
            return self.count_unit(UnitTypeId.EXTRACTOR) < self.townhalls.ready.amount * 2 - 2
        else:
            return self.count_unit(UnitTypeId.EXTRACTOR) < self.townhalls.ready.amount * 2

    async def build_spine_crawler(self):
        sc = self.units(UnitTypeId.SPINECRAWLER)
        if sc.exists:
            result = await self.build(UnitTypeId.SPINECRAWLER, sc.furthest_to(self.start_location).position, 4,
                                      placement_step=1)
        elif self.townhalls.exists:
            result = await self.build(UnitTypeId.SPINECRAWLER,
                                      self.townhalls.furthest_to(self.start_location).position.towards(
                                          self.game_info.map_center, 6), 4, placement_step=1)
        else:
            result = await self.build(UnitTypeId.SPINECRAWLER,
                                      self.start_location.towards(self.game_info.map_center, 6), 4,
                                      placement_step=1)
        return result

    def should_expand(self):
        if self.already_pending(UnitTypeId.HATCHERY) > 0:
            return False
        if self.townhalls.amount == 1 and self.supply_used < 14:
            return False
        if not self.units(UnitTypeId.SPAWNINGPOOL).exists:
            return False
        if not (self.units(UnitTypeId.ROACHWARREN).exists or self.units(UnitTypeId.BANELINGNEST).exists):
            return self.townhalls.amount <= 1
        full_workers = True
        total_ideal_harvesters = 0
        for t in self.townhalls.ready:
            t: Unit = t
            full_workers = full_workers and t.surplus_harvesters >= 0
            total_ideal_harvesters += t.ideal_harvesters
        return full_workers and total_ideal_harvesters < 16 * 4

    def enemy_unit_history_count(self, unit_types: List[UnitTypeId]) -> int:
        count = 0
        for unit_type in unit_types:
            if unit_type in self.enemy_unit_history:
                count += len(self.enemy_unit_history[unit_type])
        return count

    def is_location_safe(self, p: Point2):
        return not self.known_enemy_structures.of_type(
            {UnitTypeId.PHOTONCANNON, UnitTypeId.SPINECRAWLER, UnitTypeId.BUNKER}).closer_than(7, p).exists

    async def defend_early_rush(self) -> bool:
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures. \
            of_type({UnitTypeId.BARRACKS}).closer_than(half_size, self.start_location)
        enemy_units = self.alive_enemy_units.exclude_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        enemy_drones = self.alive_enemy_units.of_type(
            {UnitTypeId.DRONE, UnitTypeId.SCV, UnitTypeId.PROBE}).closer_than(half_size, self.start_location)
        townhall_to_defend = self.townhalls.ready.furthest_to(self.start_location)
        early_enemy_unit_count = 0.5 * self.enemy_unit_history_count(
            [UnitTypeId.ZERGLING]) + self.enemy_unit_history_count(
            [UnitTypeId.MARINE, UnitTypeId.ZEALOT, UnitTypeId.ADEPT, UnitTypeId.BANELING, UnitTypeId.REAPER])
        # build spinecrawlers
        if 1 < self.townhalls.ready.amount < 3 and \
                self.units(UnitTypeId.SPAWNINGPOOL).ready and \
                self.count_unit(UnitTypeId.SPINECRAWLER) < min(early_enemy_unit_count / 3, 3):
            await self.build_spine_crawler()
        if 0 < self.townhalls.ready.amount < 3 and (
                proxy_barracks.exists or
                enemy_units.amount > min(self.count_unit(UnitTypeId.ZERGLING), 5) or
                enemy_drones.amount > 5):
            await self.do(self.townhalls.ready.closest_to(self.start_location)(AbilityId.RALLY_HATCHERY_UNITS,
                                                                               townhall_to_defend.position))
            if self.units(UnitTypeId.ROACHWARREN).ready.exists:
                await self.train(UnitTypeId.ROACH)

            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST in abilities:
                    await self.do(sp.first(AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST))
                elif self.count_unit(UnitTypeId.SPINECRAWLER) <= 2:
                    await self.build_spine_crawler()
                else:
                    await self.train(UnitTypeId.ZERGLING)

            else:
                await self.train(UnitTypeId.DRONE)

            await self.early_attack()

            for t in self.townhalls.ready:
                t: Unit = t
                await self.dist_workers_and_inject_larva(t)

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
            if self.units(UnitTypeId.ROACHWARREN).ready.exists:
                await self.train(UnitTypeId.ROACH)

            sp = self.units(UnitTypeId.SPAWNINGPOOL).ready
            if sp.exists:
                abilities = await self.get_available_abilities(sp.first)
                if AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST in abilities:
                    await self.do(sp.first(AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST))
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(7, self.start_location).amount <= 1:
                    await self.build(
                        UnitTypeId.SPINECRAWLER,
                        self.state.mineral_field.closest_to(self.start_location).position,
                        random_alternative=False
                    )
                if self.units(UnitTypeId.SPINECRAWLER).closer_than(3, sp.first.position).amount <= 0:
                    p: Point2 = sp.first.position.random_on_distance(3)
                    if self.is_location_safe(p):
                        await self.build(
                            UnitTypeId.SPINECRAWLER,
                            sp.first.position,
                            random_alternative=False
                        )

                await self.train(UnitTypeId.ZERGLING)

            if self.should_build(UnitTypeId.SPAWNINGPOOL):
                p: Point2 = self.townhalls.furthest_to(self.start_location).position.random_on_distance(3)
                if self.is_location_safe(p):
                    await self.build(
                        UnitTypeId.SPAWNINGPOOL,
                        sp.first.position,
                        random_alternative=False
                    )

            await self.early_attack()

            for t in self.townhalls.ready:
                t: Unit = t
                await self.dist_workers_and_inject_larva(t)

            return True
        return False

    async def early_attack(self):
        half_size = self.start_location.distance_to(self.game_info.map_center)
        proxy_barracks = self.known_enemy_structures. \
            of_type({UnitTypeId.BARRACKS}).closer_than(half_size, self.start_location)

        actions = []
        if self.already_pending(UpgradeId.ZERGLINGMOVEMENTSPEED) > 0 or self.vespene > 100:
            for a in self.units(UnitTypeId.EXTRACTOR).ready:
                for w in self.empty_workers.closer_than(2.5, a):
                    actions.append(w.gather(self.state.mineral_field.closest_to(w)))

        for d in self.units(UnitTypeId.DRONE).idle:
            actions.append(d.gather(self.state.mineral_field.closest_to(d)))

        base_trade_units = set()
        for u in self.units.of_type({UnitTypeId.ROACH, UnitTypeId.ZERGLING}).tags_in(self.base_trade_units):
            u: Unit = u
            if not u.is_idle:
                base_trade_units.add(u.tag)
        self.base_trade_units = base_trade_units

        if self.forces.idle.amount > 40 and self.forces.idle.amount > self.alive_enemy_units.amount:
            for f in self.forces.idle:
                self.base_trade_units.add(f.tag)
                actions.append(f.attack(self.attack_target))

        if self.forces.idle.amount > 20 and len(self.base_trade_units) == 0:
            for f in self.forces.idle.random_group_of(10):
                f: Unit = f
                self.base_trade_units.add(f.tag)
                if proxy_barracks.exists:
                    actions.append(
                        f.move(self.start_location.closest(self.far_corners), queue=True))
                if self.enemy_expansions.exists:
                    actions.append(
                        f.attack(self.enemy_expansions.closest_to(self.enemy_start_locations[0]).position, queue=True))
                else:
                    actions.append(f.attack(self.enemy_start_locations[0], queue=True))

        await self.do_actions(actions)
