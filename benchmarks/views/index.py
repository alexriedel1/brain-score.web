from tqdm import tqdm
import re
from collections import namedtuple

import numpy as np
from colour import Color
from django.shortcuts import render

from benchmarks.models import Score, Benchmark, ModelReference, ModelMeta

colors = list(Color('red').range_to(Color('green'), 101))
# scale colors: highlight differences at the top-end of the spectrum more than at the lower end
a, b = 0.2270617, 1.321928  # fit to (0, 0), (60, 50), (100, 100)
colors = [colors[int(a * np.power(i, b))] for i in range(len(colors))]
color_suffix = '_color'
color_None = '#e0e1e2'

benchmark_order = [None, 'V1', 'V2', 'V4', 'IT', 'IT-temporal', 'behavior', 'ImageNet']


def view(request):
    benchmarks = _collect_benchmarks()
    models = _collect_models(benchmarks)
    for benchmark in benchmarks:  # remove lab for more compactness
        match = re.match(r'[^\.]+\.(.+)', benchmark.name)
        if match:
            benchmark.name = match.group(1)
        benchmark.ceiling = represent(benchmark.ceiling)
    context = {'models': models, 'benchmarks': benchmarks}
    return render(request, 'benchmarks/index.html', context)


def _collect_benchmarks():
    benchmarks = Benchmark.objects.all()
    # filter to benchmarks that we have scores for
    score_benchmarks = Score.objects.values_list('benchmark', flat=True).distinct()
    benchmarks = [benchmark for benchmark in benchmarks if benchmark.name in score_benchmarks]
    # sort benchmarks
    benchmarks = _order(benchmarks, identifier_fnc=lambda benchmark: benchmark.parent)
    return benchmarks


def _collect_models(benchmarks):
    # pre-compute aggregates
    benchmarks_meta = {}
    for benchmark in [benchmark.name for benchmark in benchmarks]:
        benchmark_scores = Score.objects.filter(benchmark=benchmark)
        benchmark_scores = [score.score_ceiled if score.score_ceiled is not None else 0 for score in benchmark_scores]
        min_value, max_value = min(benchmark_scores), max(benchmark_scores)
        ceiling = Benchmark.objects.filter(name=benchmark)[0].ceiling
        benchmarks_meta[benchmark] = {'ceiling': ceiling, 'min': min_value, 'max': max_value}

    # arrange scores
    scores = Score.objects.all().select_related()
    ModelRow = namedtuple('ModelRow', field_names=[
        'name', 'reference_identifier', 'reference_link', 'meta', 'rank', 'scores'])
    ScoreDisplay = namedtuple('ScoreDiplay', field_names=[
        'benchmark', 'score_raw', 'score_ceiled', 'color', 'layer'])

    data = {}
    for score in tqdm(scores, desc='scores'):
        if score.benchmark not in benchmarks_meta:
            continue

        # if model information is not present yet, fill it
        if score.model not in data:
            index = re.search(r'(--)', score.model)
            model_base_identifier = score.model[:index.start()] if index else score.model
            reference = ModelReference.objects.filter(model=model_base_identifier)
            reference = reference[0] if len(reference) > 0 else None
            meta = ModelMeta.objects.filter(model=model_base_identifier)
            meta = _order(meta, identifier_fnc=lambda meta_row: [
                prefix for prefix in benchmark_order if isinstance(prefix, str) and meta_row.key.startswith(prefix)][0])
            meta = '\n'.join([f"{meta_row.key}: {meta_row.value}" for meta_row in meta])
            data[score.model] = ModelRow(name=score.model,
                                         reference_identifier=reference.short_reference if reference else None,
                                         reference_link=reference.link if reference else None,
                                         meta=meta,
                                         rank=None,
                                         scores={})

        benchmark_meta = benchmarks_meta[score.benchmark]
        color = representative_color(score.score_ceiled,
                                     alpha_min=benchmark_meta['min'], alpha_max=benchmark_meta['max'])
        score_ceiled, score_raw = represent(score.score_ceiled), represent(score.score_raw)
        score_display = ScoreDisplay(benchmark=score.benchmark, score_ceiled=score_ceiled, score_raw=score_raw,
                                     color=color, layer=score.layer)
        data[score.model].scores[score.benchmark] = score_display

    # sort score benchmarks
    no_score, error_score = {}, {}
    for benchmark in benchmarks:
        meta = benchmarks_meta[benchmark.name]
        no_score[benchmark.name] = ScoreDisplay(
            benchmark=benchmark.name, score_ceiled="", score_raw="",
            color=representative_color(None, alpha_min=meta['min'], alpha_max=meta['max']),
            layer="not yet run")
        error_score[benchmark.name] = ScoreDisplay(
            benchmark=benchmark.name, score_ceiled="X", score_raw="X",
            color=representative_color(None, alpha_min=meta['min'], alpha_max=meta['max']),
            layer=None)
    data = [model_row._replace(scores=[
        model_row.scores[benchmark.name] if benchmark.name in model_row.scores else no_score[benchmark.name]
        for benchmark in benchmarks])
        for model_row in tqdm(data.values(), desc='sort benchmarks')]

    # infer rank
    average_scores = {model_row.name: [score.score_ceiled for score in model_row.scores
                                       if score.benchmark == 'average'][0]
                      for model_row in data}
    all_scores = list(sorted(average_scores.values(), reverse=True))
    ranks = {model: all_scores.index(score) + 1 for model, score in average_scores.items()}
    data = [model_row._replace(rank=ranks[model_row.name]) for model_row in tqdm(data, desc='ranking')]
    return data


def _order(values, identifier_fnc):
    return [value for parent_index, value in sorted(zip(
        [benchmark_order.index(identifier_fnc(value)) for value in values],
        values))]


def normalize(value, min_value, max_value):
    # intercept and slope equations are from solving `y = slope * x + intercept`
    # with points [min_value, 10] (10 instead of 0 to not make it completely transparent) and [max_value, 100].
    slope = -.9 / (min_value - max_value)
    intercept = .1 - slope * min_value
    result = slope * value + intercept
    return result


def represent(value):
    if value is None:
        return "X"
    return "{:.3f}".format(value).lstrip('0') if value < 1 else "{:.1f}".format(value)


def representative_color(value, alpha_min=None, alpha_max=None):
    if value is None:
        return f"background-color: {color_None}"
    step = int(100 * value)
    color = colors[step]
    color = tuple(c * 255 for c in color.rgb)
    fallback_color = tuple(round(c) for c in color)
    normalized_value = normalize(value, min_value=alpha_min, max_value=alpha_max) \
        if alpha_min is not None else (100 * value)
    color += (normalized_value,)
    return f"background-color: rgb{fallback_color}; background-color: rgba{color};"
