import json
from pathlib import Path

import random
import sc2
from sc2.constants import *
from sc2.unit import Unit


class MyBot(sc2.BotAI):
    with open(Path(__file__).parent / "../botinfo.json") as f:
        NAME = json.load(f)["name"]

    def select_target(self):
        if self.known_enemy_structures.exists:
            return random.choice(self.known_enemy_structures).position

        return self.enemy_start_locations[0]

    async def on_step(self, iteration):
        larvae = self.units(LARVA)
        forces = self.units(ZERGLING) | self.units(HYDRALISK)

        if self.townhalls.amount <= 0:
            for unit in self.units(DRONE) | self.units(QUEEN) | forces:
                await self.do(unit.attack(self.enemy_start_locations[0]))
            return
        else:
            hq = self.townhalls.closest_to(self.start_location)

        for t in self.townhalls.ready:
            t: Unit = t

            if t.assigned_harvesters < t.ideal_harvesters:
                if self.can_afford(DRONE) and larvae.exists:
                    await self.do(larvae.random.train(DRONE))
                    return

            excess_worker = self.workers.closer_than(10, t.position)
            m = self.need_worker_mineral()
            if t.assigned_harvesters > t.ideal_harvesters and excess_worker.exists and m is not None:
                await self.do(excess_worker.random.gather(m))

            if (self.units(SPAWNINGPOOL).ready.exists and
                    t.is_ready and
                    t.noqueue and
                    not self.units(QUEEN).closer_than(10, t.position).exists and
                    self.can_afford(QUEEN)):
                await self.do(t.train(QUEEN))

            queen_nearby = self.units(QUEEN).idle.closer_than(10, t.position)
            if queen_nearby.exists:
                queen = queen_nearby.first
                abilities = await self.get_available_abilities(queen)
                if AbilityId.EFFECT_INJECTLARVA in abilities:
                    await self.do(queen(EFFECT_INJECTLARVA, t))

        if self.supply_used > 190:
            for unit in forces.idle:
                await self.do(unit.attack(self.select_target()))
        else:
            far_h = self.townhalls.furthest_to(self.start_location)
            for unit in forces.further_than(10, far_h.position):
                await self.do(unit.move(far_h.position.random_on_distance(5)))

        # supply_cap does not include overload that is being built
        if (self.units(OVERLORD).amount + self.already_pending(OVERLORD)) * 8 - self.supply_used < 2:
            if self.can_afford(OVERLORD) and larvae.exists:
                await self.do(larvae.random.train(OVERLORD))
                return

        sp = self.units(SPAWNINGPOOL).ready
        if sp.exists:
            if (ZERGLINGMOVEMENTSPEED not in self.state.upgrades) and self.can_afford(ZERGLINGMOVEMENTSPEED):
                await self.do(sp.first.research(ZERGLINGMOVEMENTSPEED))
            if not self.units(LAIR).exists and hq.noqueue:
                if self.can_afford(LAIR):
                    await self.do(hq.build(LAIR))

        hd = self.units(HYDRALISKDEN).ready
        if hd.exists:
            if (EVOLVEMUSCULARAUGMENTS not in self.state.upgrades) and self.can_afford(EVOLVEMUSCULARAUGMENTS):
                await self.do(hd.first.research(EVOLVEMUSCULARAUGMENTS))
            if (EVOLVEGROOVEDSPINES not in self.state.upgrades) and self.can_afford(EVOLVEGROOVEDSPINES):
                await self.do(hd.first.research(EVOLVEGROOVEDSPINES))
            if self.can_afford(HYDRALISK) and larvae.exists:
                await self.do(larvae.random.train(HYDRALISK))
                return

        if not (self.units(SPAWNINGPOOL).exists or self.already_pending(SPAWNINGPOOL)):
            if self.can_afford(SPAWNINGPOOL):
                await self.build(SPAWNINGPOOL, near=hq)

        if self.should_expand():
            await self.expand_now(HATCHERY)

        if self.units(LAIR).ready.exists:
            if not (self.units(HYDRALISKDEN).exists or self.already_pending(HYDRALISKDEN)):
                if self.can_afford(HYDRALISKDEN):
                    await self.build(HYDRALISKDEN, near=hq)

        if (self.units(EXTRACTOR).amount < self.townhalls.amount * 2 - 2 and
                not self.already_pending(EXTRACTOR)):
            if self.can_afford(EXTRACTOR):
                drone = self.workers.random
                target = self.state.vespene_geyser.closest_to(drone.position)
                err = await self.do(drone.build(EXTRACTOR, target))

        for a in self.units(EXTRACTOR):
            if a.assigned_harvesters < a.ideal_harvesters:
                w = self.workers.closer_than(20, a)
                if w.exists:
                    await self.do(w.random.gather(a))

        for d in self.units(DRONE).idle:
            d: Unit = d
            mf = self.state.mineral_field.closest_to(d.position)
            await self.do(d.gather(mf))

        if self.units(ZERGLING).amount < 20 and self.minerals - self.vespene > 500:
            if larvae.exists and self.can_afford(ZERGLING):
                await self.do(larvae.random.train(ZERGLING))
                return

    def need_worker_mineral(self):
        t = self.townhalls.ready.filter(lambda a: a.assigned_harvesters < a.ideal_harvesters)
        if t.exists:
            return self.state.mineral_field.closest_to(t.random.position)
        else:
            return None

    def should_expand(self):
        if self.minerals < 300 or self.already_pending(HATCHERY):
            return False
        if self.units(SPAWNINGPOOL).exists and self.townhalls.amount < 2:
            return True
        if self.units(HYDRALISKDEN).exists and self.townhalls.amount < 3:
            return True
        return self.units(DRONE).collecting.amount < 66
