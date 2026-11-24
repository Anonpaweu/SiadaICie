package pl.iccbagno.app.optimizer

import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.StationInfo
import pl.iccbagno.app.scraper.GrmParser
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

enum class OptimizationProfile(val label: String) {
    BALANCED("Zbalansowany"),
    MINIMAL_WALKING("Minimalne chodzenie"),
    MINIMAL_TRANSFERS("Minimum przesiadek")
}

data class SeatMeta(
    val seatUid: String,
    val carriageNo: Int,
    val seatNo: String,
    val compartmentNo: Int? = null
)

data class AvailabilityBlock(
    val blockId: String,
    val seat: SeatMeta,
    val startSeg: Int,
    val endSeg: Int,
    val durationMin: Int,
    val isStanding: Boolean = false
)

data class PathStep(
    val seatUid: String,
    val carriageNo: Int,
    val seatNo: String,
    val fromStationIdx: Int,
    val toStationIdx: Int,
    val fromStationName: String,
    val toStationName: String,
    val durationMin: Int,
    val isStanding: Boolean = false,
    val transferInstruction: String? = null,
    val compartment: Int? = null,
    val alternativeSeatsInSameCarriage: List<String> = emptyList(),
    val bookingUrl: String? = null
)

data class OptimizationStrategy(
    val rank: Int,
    val title: String,
    val totalScore: Double,
    val transfersCount: Int,
    val totalStandingMin: Int,
    val carriagesUsed: List<Int>,
    val steps: List<PathStep>,
    val ticketsToBuy: List<String>,
    val purchaseInstructions: List<String>
)

data class OptimizerConfig(
    val profile: OptimizationProfile = OptimizationProfile.BALANCED,
    val wDuration: Double = 1000.0,
    val wFinalBonus: Double = 250.0,
    val pBaseTransfer: Double = 1000.0,
    val pStandingPerMin: Double = 25.0,
    val wWarsCrossingPenalty: Double = 80.0,
    val warsCarriageNo: Int? = null,
    val disconnectedCarriages: List<Pair<Int, Int>> = emptyList()
)

data class MatrixData(
    val availabilityMatrix: Map<String, List<Int>>,
    val seatsMeta: Map<String, SeatMeta>,
    val stationNames: List<String>,
    val segmentDurations: List<Int>
)

object SeatOptimizer {

    fun parseProfile(raw: String?): OptimizationProfile {
        if (raw.isNullOrBlank()) return OptimizationProfile.BALANCED
        return try {
            OptimizationProfile.valueOf(raw.trim().uppercase())
        } catch (_: IllegalArgumentException) {
            OptimizationProfile.BALANCED
        }
    }

    fun getProfileMultipliers(profile: OptimizationProfile): Pair<Double, Double> {
        return when (profile) {
            OptimizationProfile.BALANCED -> 1.0 to 1.0
            OptimizationProfile.MINIMAL_WALKING -> 4.0 to 1.0
            OptimizationProfile.MINIMAL_TRANSFERS -> 0.5 to 3.0
        }
    }

    fun formatTransfersPl(count: Int): String {
        if (count <= 0) return "bez przesiadek"
        if (count == 1) return "1 przesiadka"
        val mod10 = count % 10
        val mod100 = count % 100
        if (mod10 in 2..4 && mod100 !in 12..14) {
            return "$count przesiadki"
        }
        return "$count przesiadek"
    }

    fun mergeConsecutiveStandingBlocks(path: List<AvailabilityBlock>): List<AvailabilityBlock> {
        val merged = ArrayList<AvailabilityBlock>(path.size)
        for (block in path) {
            val prev = merged.lastOrNull()
            if (block.isStanding && prev != null && prev.isStanding && prev.endSeg == block.startSeg) {
                merged[merged.lastIndex] = AvailabilityBlock(
                    blockId = "STANDING_${prev.startSeg}_${block.endSeg}",
                    seat = prev.seat,
                    startSeg = prev.startSeg,
                    endSeg = block.endSeg,
                    durationMin = prev.durationMin + block.durationMin,
                    isStanding = true
                )
            } else {
                merged.add(block)
            }
        }
        return merged
    }

    fun calculateTransferPenalty(
        seatA: SeatMeta,
        seatB: SeatMeta,
        isStandingB: Boolean,
        config: OptimizerConfig
    ): Double {
        val (distMult, transferMult) = getProfileMultipliers(config.profile)
        val baseTransfer = config.pBaseTransfer * transferMult

        if (seatA.seatUid == "STANDING" && (isStandingB || seatB.seatUid == "STANDING")) {
            return 0.0
        }
        if (isStandingB || seatA.seatUid == "STANDING" || seatB.seatUid == "STANDING") {
            return baseTransfer + 100.0
        }
        if (seatA.seatUid == seatB.seatUid) {
            return 0.0
        }

        val cA = seatA.carriageNo
        val cB = seatB.carriageNo
        val deltaC = abs(cA - cB)

        for ((d1, d2) in config.disconnectedCarriages) {
            val low = min(d1, d2)
            val high = max(d1, d2)
            if (min(cA, cB) <= low && max(cA, cB) >= high) {
                return baseTransfer + 600.0 * distMult
            }
        }

        if (deltaC == 0) {
            val distPenalty = if (seatA.compartmentNo != null &&
                seatB.compartmentNo != null &&
                seatA.compartmentNo == seatB.compartmentNo
            ) {
                15.0
            } else {
                40.0
            }
            return baseTransfer + distPenalty * distMult
        }

        var distPenalty = 100.0 * (deltaC * deltaC).toDouble()
        val wars = config.warsCarriageNo
        if (wars != null && min(cA, cB) < wars && wars < max(cA, cB)) {
            distPenalty += config.wWarsCrossingPenalty
        }
        return baseTransfer + distPenalty * distMult
    }

    private data class EquivalenceKey(
        val carriageNo: Int,
        val compartmentNo: Int?,
        val vec: List<Int>
    )

    fun pruneEquivalentSeats(
        availabilityMatrix: Map<String, List<Int>>,
        seatsMeta: Map<String, SeatMeta>,
        excludedSeats: Set<String>,
        excludedCarriages: Set<Int>
    ): Map<String, List<Int>> {
        val groupedCounts = HashMap<EquivalenceKey, Int>()
        val pruned = LinkedHashMap<String, List<Int>>()
        for ((seatUid, vec) in availabilityMatrix) {
            if (seatUid in excludedSeats) continue
            val meta = seatsMeta[seatUid] ?: continue
            if (meta.carriageNo in excludedCarriages) continue
            val key = EquivalenceKey(meta.carriageNo, meta.compartmentNo, vec)
            val cnt = groupedCounts[key] ?: 0
            if (cnt < 2) {
                groupedCounts[key] = cnt + 1
                pruned[seatUid] = vec
            }
        }
        return pruned
    }

    fun generateCandidateBlocks(
        availabilityMatrix: Map<String, List<Int>>,
        seatsMeta: Map<String, SeatMeta>,
        segmentDurations: List<Int>
    ): List<AvailabilityBlock> {
        val numSegments = segmentDurations.size
        val blocks = ArrayList<AvailabilityBlock>()

        for ((seatUid, segVector) in availabilityMatrix) {
            val meta = seatsMeta[seatUid] ?: continue
            for (i in 0 until numSegments) {
                if (segVector[i] != 1) continue
                var currentDur = 0
                for (j in (i + 1)..numSegments) {
                    if (segVector[j - 1] != 1) break
                    currentDur += segmentDurations[j - 1]
                    blocks.add(
                        AvailabilityBlock(
                            blockId = "${seatUid}_${i}_$j",
                            seat = meta,
                            startSeg = i,
                            endSeg = j,
                            durationMin = currentDur,
                            isStanding = false
                        )
                    )
                }
            }
        }

        val standingMeta = SeatMeta(seatUid = "STANDING", carriageNo = 0, seatNo = "KORYTARZ")
        for (i in 0 until numSegments) {
            blocks.add(
                AvailabilityBlock(
                    blockId = "STANDING_${i}_${i + 1}",
                    seat = standingMeta,
                    startSeg = i,
                    endSeg = i + 1,
                    durationMin = segmentDurations[i],
                    isStanding = true
                )
            )
        }
        return blocks
    }

    fun scoreSingleBlock(
        block: AvailabilityBlock,
        totalTripMin: Int,
        numSegments: Int,
        config: OptimizerConfig
    ): Double {
        if (block.isStanding) {
            return -config.pStandingPerMin * block.durationMin
        }
        val ratio = block.durationMin.toDouble() / max(1.0, totalTripMin.toDouble())
        var score = config.wDuration * ratio.pow(1.6)
        if (block.endSeg == numSegments) {
            score += config.wFinalBonus * ratio
        }
        return score
    }

    fun findOptimalPath(
        blocks: List<AvailabilityBlock>,
        segmentDurations: List<Int>,
        config: OptimizerConfig,
        excludedPrimarySeats: Set<String> = emptySet(),
        excludedCarriages: Set<Int> = emptySet()
    ): Pair<Double, List<AvailabilityBlock>>? {
        val numSegments = segmentDurations.size
        if (numSegments == 0) return null
        val totalTripMin = segmentDurations.sum()

        val activeBlocks = blocks.filter { b ->
            b.isStanding ||
                (b.seat.seatUid !in excludedPrimarySeats && b.seat.carriageNo !in excludedCarriages)
        }

        val blocksByStart = Array(numSegments) { ArrayList<AvailabilityBlock>() }
        val blockMap = HashMap<String, AvailabilityBlock>(activeBlocks.size)
        for (b in activeBlocks) {
            if (b.startSeg in 0 until numSegments) {
                blocksByStart[b.startSeg].add(b)
                blockMap[b.blockId] = b
            }
        }

        val dp = HashMap<String, Pair<Double, String?>>(activeBlocks.size)

        for (sIdx in (numSegments - 1) downTo 0) {
            for (b in blocksByStart[sIdx]) {
                val bScore = scoreSingleBlock(b, totalTripMin, numSegments, config)
                if (b.endSeg == numSegments) {
                    dp[b.blockId] = bScore to null
                    continue
                }

                var bestNextVal = -1e12
                var bestNextId: String? = null
                for (nextB in blocksByStart[b.endSeg]) {
                    if (!b.isStanding && b.seat.seatUid == nextB.seat.seatUid) continue
                    val nextDp = dp[nextB.blockId] ?: continue
                    val penalty = calculateTransferPenalty(b.seat, nextB.seat, nextB.isStanding, config)
                    val candidateVal = bScore - penalty + nextDp.first
                    if (candidateVal > bestNextVal) {
                        bestNextVal = candidateVal
                        bestNextId = nextB.blockId
                    }
                }
                if (bestNextId != null) {
                    dp[b.blockId] = bestNextVal to bestNextId
                }
            }
        }

        var bestTotalScore = -1e12
        var bestStartId: String? = null
        for (b in blocksByStart[0]) {
            val res = dp[b.blockId] ?: continue
            if (res.first > bestTotalScore) {
                bestTotalScore = res.first
                bestStartId = b.blockId
            }
        }
        if (bestStartId == null) return null

        val path = ArrayList<AvailabilityBlock>()
        var currId: String? = bestStartId
        while (currId != null) {
            val block = blockMap[currId] ?: break
            path.add(block)
            currId = dp[currId]?.second
        }
        return bestTotalScore to path
    }

    fun availabilityMapToMatrix(availMap: FullTrainAvailabilityMap): MatrixData {
        val segs = availMap.segments
        if (segs.isEmpty()) return MatrixData(emptyMap(), emptyMap(), emptyList(), emptyList())

        val stationNames = ArrayList<String>(segs.size + 1)
        stationNames.add(segs[0].fromStation)
        for (s in segs) stationNames.add(s.toStation)

        val segmentDurations = segs.map { seg ->
            if (seg.durationMin > 0) seg.durationMin.coerceAtLeast(5) else 30
        }

        val allSeats = HashSet<Pair<String, String>>()
        for (seg in segs) {
            for ((cNo, seats) in seg.carriages) {
                for (sNo in seats) {
                    allSeats.add(cNo to sNo)
                }
            }
        }

        val sortedSeats = allSeats.sortedWith(
            compareBy<Pair<String, String>>(
                { it.first.toIntOrNull() ?: 99 },
                { it.second.toIntOrNull() ?: 999 }
            )
        )

        val availMatrix = LinkedHashMap<String, List<Int>>(sortedSeats.size)
        val seatsMeta = LinkedHashMap<String, SeatMeta>(sortedSeats.size)

        for ((cNo, sNo) in sortedSeats) {
            val seatUid = "${cNo}_$sNo"
            val sInt = sNo.toIntOrNull() ?: 1
            val cInt = cNo.toIntOrNull() ?: 1
            val comp = if (sInt >= 10) sInt / 10 else 1
            seatsMeta[seatUid] = SeatMeta(seatUid, cInt, sNo, comp)
            availMatrix[seatUid] = segs.map { seg ->
                val freeInC = seg.carriages[cNo].orEmpty()
                if (sNo in freeInC) 1 else 0
            }
        }

        return MatrixData(availMatrix, seatsMeta, stationNames, segmentDurations)
    }

    fun solveTopStrategies(
        availabilityMatrix: Map<String, List<Int>>,
        seatsMeta: Map<String, SeatMeta>,
        stationNames: List<String>,
        segmentDurations: List<Int>,
        config: OptimizerConfig = OptimizerConfig(),
        topK: Int = 3,
        stationEvas: Map<String, String> = emptyMap(),
        departureTimes: List<String> = emptyList(),
        segmentDates: List<String> = emptyList(),
        defaultTravelDate: String = ""
    ): List<OptimizationStrategy> {
        val strategies = ArrayList<OptimizationStrategy>()
        val excludedSeats = HashSet<String>()
        val excludedCarriages = HashSet<Int>()
        val seenSignatures = HashSet<List<Triple<String, Int, Int>>>()

        for (rank in 1..topK) {
            var res: Pair<Double, List<AvailabilityBlock>>? = null
            if (excludedCarriages.isNotEmpty()) {
                val prunedDiv = pruneEquivalentSeats(availabilityMatrix, seatsMeta, excludedSeats, excludedCarriages)
                if (prunedDiv.isNotEmpty()) {
                    val blocksDiv = generateCandidateBlocks(prunedDiv, seatsMeta, segmentDurations)
                    val candidate = findOptimalPath(blocksDiv, segmentDurations, config)
                    if (candidate != null && candidate.second.none { it.isStanding }) {
                        res = candidate
                    }
                }
            }

            if (res == null) {
                val pruned = pruneEquivalentSeats(availabilityMatrix, seatsMeta, excludedSeats, emptySet())
                val blocks = generateCandidateBlocks(pruned, seatsMeta, segmentDurations)
                res = findOptimalPath(blocks, segmentDurations, config)
            }
            if (res == null) break

            val totalScore = res.first
            val path = mergeConsecutiveStandingBlocks(res.second)
            val sig = path.map { Triple(it.seat.seatUid, it.startSeg, it.endSeg) }
            if (!seenSignatures.add(sig)) break

            val steps = ArrayList<PathStep>(path.size)
            val carriagesUsed = ArrayList<Int>()
            var standingMin = 0

            for (idx in path.indices) {
                val b = path[idx]
                if (b.isStanding) {
                    standingMin += b.durationMin
                } else if (b.seat.carriageNo !in carriagesUsed) {
                    carriagesUsed.add(b.seat.carriageNo)
                }

                val instruction = if (idx > 0) {
                    val prevB = path[idx - 1]
                    val stName = stationNames[b.startSeg]
                    when {
                        b.isStanding ->
                            "Na stacji $stName zwolnij miejsce ${prevB.seat.seatNo} (przejdź na korytarz na ${b.durationMin} min)."
                        prevB.isStanding ->
                            "Na stacji $stName zajmij miejsce ${b.seat.seatNo} w wagonie ${b.seat.carriageNo}."
                        prevB.seat.carriageNo == b.seat.carriageNo ->
                            "Na stacji $stName przesiądź się w tym samym wagonie (${b.seat.carriageNo}) z miejsca ${prevB.seat.seatNo} na miejsce ${b.seat.seatNo}."
                        else ->
                            "Na stacji $stName przejdź z wagonu ${prevB.seat.carriageNo} (m. ${prevB.seat.seatNo}) do wagonu ${b.seat.carriageNo} na miejsce ${b.seat.seatNo}."
                    }
                } else null

                val altSeats = ArrayList<String>()
                if (!b.isStanding) {
                    for ((oUid, oVec) in availabilityMatrix) {
                        if (oUid != b.seat.seatUid) {
                            val oMeta = seatsMeta[oUid]
                            if (oMeta != null && oMeta.carriageNo == b.seat.carriageNo) {
                                if ((b.startSeg until b.endSeg).all { k -> oVec[k] == 1 }) {
                                    altSeats.add(oMeta.seatNo)
                                }
                            }
                        }
                    }
                }

                val sInt = b.seat.seatNo.toIntOrNull()
                val compNo = b.seat.compartmentNo ?: if (sInt != null) { if (sInt >= 10) sInt / 10 else 1 } else null

                val fromName = stationNames[b.startSeg]
                val toName = stationNames[b.endSeg]
                val bookingUrl = if (!b.isStanding && defaultTravelDate.isNotBlank()) {
                    val fEva = stationEvas[fromName].orEmpty()
                    val tEva = stationEvas[toName].orEmpty()
                    if (fEva.isNotEmpty() && tEva.isNotEmpty()) {
                        val depT = departureTimes.getOrNull(b.startSeg)?.takeIf { it.isNotBlank() } ?: "08:00"
                        val sDate = segmentDates.getOrNull(b.startSeg)?.takeIf { it.isNotBlank() } ?: defaultTravelDate
                        GrmParser.buildEicSearchUrl(fEva, tEva, sDate, depT, directOnly = true)
                    } else null
                } else null

                steps.add(
                    PathStep(
                        seatUid = b.seat.seatUid,
                        carriageNo = b.seat.carriageNo,
                        seatNo = b.seat.seatNo,
                        fromStationIdx = b.startSeg,
                        toStationIdx = b.endSeg,
                        fromStationName = fromName,
                        toStationName = toName,
                        durationMin = b.durationMin,
                        isStanding = b.isStanding,
                        transferInstruction = instruction,
                        compartment = compNo,
                        alternativeSeatsInSameCarriage = altSeats.sortedBy { it.toIntOrNull() ?: 99 },
                        bookingUrl = bookingUrl
                    )
                )
            }

            val nonStandingBlocks = path.filter { !it.isStanding }
            if (nonStandingBlocks.isNotEmpty()) {
                val longestBlock = nonStandingBlocks.maxBy { it.durationMin }
                excludedSeats.add(longestBlock.seat.seatUid)
                if (carriagesUsed.size == 1) {
                    excludedCarriages.add(carriagesUsed[0])
                }
            } else {
                break
            }

            val transfersCount = max(0, nonStandingBlocks.size - 1)
            val title = when {
                transfersCount == 0 && standingMin == 0 ->
                    "100% Trasy bez przesiadek (Złoty Strzał)"
                carriagesUsed.size == 1 && standingMin == 0 ->
                    "Przesiadka w tym samym wagonie (Wagon ${carriagesUsed[0]}, ${formatTransfersPl(transfersCount)})"
                standingMin > 0 && transfersCount == 0 ->
                    "1 miejsce siedzące + korytarz ($standingMin min)"
                standingMin > 0 ->
                    "Optymalna kombinacja (${formatTransfersPl(transfersCount)}, korytarz $standingMin min)"
                else ->
                    "Optymalna kombinacja (${formatTransfersPl(transfersCount)})"
            }

            val ticketsToBuy = ArrayList<String>()
            val purchaseInstructions = ArrayList<String>()
            var ticketNr = 1
            for (s in steps) {
                if (!s.isStanding) {
                    ticketsToBuy.add("Bilet $ticketNr: ${s.fromStationName} -> ${s.toStationName} (Wagon ${s.carriageNo}, Miejsce ${s.seatNo})")
                    var instr = "Krok $ticketNr: Kup bilet na odcinek ${s.fromStationName} -> ${s.toStationName}: wybierz Wagon ${s.carriageNo}, Miejsce ${s.seatNo}"
                    if (s.alternativeSeatsInSameCarriage.isNotEmpty()) {
                        instr += " (lub inne wolne w tym wagonie: ${s.alternativeSeatsInSameCarriage.take(3).joinToString(", ") { "m$it" }})"
                    }
                    purchaseInstructions.add(instr)
                    ticketNr++
                } else {
                    purchaseInstructions.add("Odcinek ${s.fromStationName} -> ${s.toStationName}: brak miejsc siedzących - przejście na korytarz (${s.durationMin} min)")
                }
            }

            strategies.add(
                OptimizationStrategy(
                    rank = rank,
                    title = title,
                    totalScore = (totalScore * 100.0).toLong() / 100.0,
                    transfersCount = transfersCount,
                    totalStandingMin = standingMin,
                    carriagesUsed = carriagesUsed,
                    steps = steps,
                    ticketsToBuy = ticketsToBuy,
                    purchaseInstructions = purchaseInstructions
                )
            )
        }
        return strategies
    }
}
