import logging
import os
from typing import List, Dict, Union, Tuple, Any

from octopoes.models import OOI
from pydantic import ValidationError

from boefjes.job_models import (
    BoefjeMeta,
    NormalizerMeta,
    NormalizerOutput,
    NormalizerPlainOOI,
    ObservationsWithoutInputOOI,
    NormalizerObservation,
    NormalizerDeclaration,
    UnsupportedReturnTypeNormalizer,
    InvalidReturnValueNormalizer,
    NormalizerResult,
)
from boefjes.katalogus.local_repository import LocalPluginRepository
from boefjes.runtime_interfaces import BoefjeJobRunner, NormalizerJobRunner

logger = logging.getLogger(__name__)


class TemporaryEnvironment:
    def __init__(self):
        self._original_environment = os.environ.copy()

    def __enter__(self):
        return os.environ

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.environ = self._original_environment


class LocalBoefjeJobRunner(BoefjeJobRunner):
    def __init__(self, local_repository: LocalPluginRepository):
        self.local_repository = local_repository

    def run(self, boefje_meta: BoefjeMeta, environment: Dict[str, str]) -> Tuple[BoefjeMeta, Union[str, bytes]]:
        logger.info("Running local boefje plugin")

        boefjes = self.local_repository.resolve_boefjes()
        boefje_resource = boefjes[boefje_meta.boefje.id]

        with TemporaryEnvironment() as temporary_environment:
            temporary_environment.update(environment)
            return boefje_resource.module.run(boefje_meta)


class LocalNormalizerJobRunner(NormalizerJobRunner):
    def __init__(self, local_repository: LocalPluginRepository):
        self.local_repository = local_repository

    def run(self, normalizer_meta, raw) -> NormalizerOutput:
        logger.info("Running local normalizer plugin")

        normalizers = self.local_repository.resolve_normalizers()
        normalizer = normalizers[normalizer_meta.normalizer.id]

        return self._parse_results(normalizer_meta, normalizer.module.run(normalizer_meta, raw))

    def _parse_results(self, normalizer_meta: NormalizerMeta, results: List[Any]) -> NormalizerOutput:
        parsed: List[NormalizerResult] = [self._parse(result) for result in results]

        if oois := [ooi for ooi in parsed if isinstance(ooi.item, NormalizerPlainOOI)]:
            if not normalizer_meta.raw_data.boefje_meta.input_ooi:
                raise ObservationsWithoutInputOOI(normalizer_meta)

            # For both compatibility and ease of use, this makes sure we support normalizers only returning OOI dicts.
            parsed.append(
                NormalizerResult(
                    item=NormalizerObservation(
                        type="observation",
                        input_ooi=normalizer_meta.raw_data.boefje_meta.input_ooi,
                        results=[result.item for result in oois],
                    )
                )
            )

        observations = [result.item for result in parsed if isinstance(result.item, NormalizerObservation)]

        if observations and not normalizer_meta.raw_data.boefje_meta.input_ooi:
            raise ObservationsWithoutInputOOI(normalizer_meta)

        return NormalizerOutput(
            observations=observations,
            declarations=[result.item for result in parsed if isinstance(result.item, NormalizerDeclaration)],
        )

    @staticmethod
    def _parse(result: Any) -> NormalizerResult:
        if not isinstance(result, dict):  # Must be an OOI. This should be phased out together with Octopoes
            if not isinstance(result, OOI):
                raise UnsupportedReturnTypeNormalizer(str(type(result)))

            result = result.dict()

        try:
            return NormalizerResult(item=result)
        except ValidationError as e:
            raise InvalidReturnValueNormalizer(e.json())
