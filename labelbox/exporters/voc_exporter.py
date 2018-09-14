"""
Module for converting labelbox.com JSON exports to Pascal VOC 2012 format.
"""

import json
import logging
import os
from PIL import Image
import requests
from shapely import wkt
from typing import Any, Sequence

from labelbox.exceptions import UnknownFormatError
from labelbox.exporters.pascal_voc_writer import Writer as PascalWriter


def from_json(labeled_data, annotations_output_dir, images_output_dir,
              label_format='WKT'):
    """Convert Labelbox JSON export to Pascal VOC format.

    Args:
        labeled_data (str): File path to Labelbox JSON export of label data.
        annotations_output_dir (str): File path of directory to write Pascal VOC
            annotation files.
        images_output_dir (str): File path of directory to write images.
        label_format (str): Format of the labeled data.
            Valid options are: "WKT" and "XY", default is "WKT".

    Todo:
        * Add functionality to allow use of local copy of an image instead of
            downloading it each time.
    """

    # make sure annotation output directory is valid
    try:
        annotations_output_dir = os.path.abspath(annotations_output_dir)
        assert os.path.isdir(annotations_output_dir)
    except AssertionError:
        logging.exception('Annotation output directory does not exist')

    # read labelbox JSON output
    with open(labeled_data, 'r') as file_handle:
        lines = file_handle.readlines()
        label_data = json.loads(lines[0])

    for data in label_data:
        try:
            write_label(
                data['ID'],
                data['Labeled Data'],
                data['Label'],
                label_format,
                images_output_dir,
                annotations_output_dir)

        except requests.exceptions.MissingSchema as exc:
            logging.exception(exc)
            continue
        except requests.exceptions.ConnectionError:
            logging.exception('Failed to fetch image from %s', data['Labeled Data'])
            continue


def write_label(
        label_id: str, image_url: str, labels: Sequence[Any], label_format: str,
        images_output_dir: str, annotations_output_dir: str):
    """Writes a single Pascal VOC formatted image and label pair to disk.

    Args:
        label_id: ID for the instance to write
        image_url: URL to download image file from
        labels: Labelbox formatted labels to use for generating annotation
        label_format: Format of the labeled data. Valid options are: "WKT" and "XY", default is "WKT".
        annotations_output_dir: File path of directory to write Pascal VOC
            annotation files.
        images_output_dir: File path of directory to write images.
    """
    # Download image and save it
    response = requests.get(image_url, stream=True)
    response.raw.decode_content = True
    image = Image.open(response.raw)
    image_name = ('{img_id}.{ext}'.format(img_id=label_id, ext=image.format.lower()))
    image_fqn = os.path.join(images_output_dir, image_name)
    image.save(image_fqn, format=image.format)

    # generate image annotation in Pascal VOC
    width, height = image.size
    xml_writer = PascalWriter(image_fqn, width, height)

    # remove classification labels (Skip, etc...)
    if not callable(getattr(labels, 'keys', None)):
        # skip if no categories (e.g. "Skip")
        return

    # convert label to Pascal VOC format
    for category_name, paths in labels.items():
        if label_format == 'WKT':
            xml_writer = _add_pascal_object_from_wkt(
                xml_writer, img_height=height, wkt_data=paths,
                label=category_name)
        elif label_format == 'XY':
            xml_writer = _add_pascal_object_from_xy(
                xml_writer, img_height=height, polygons=paths,
                label=category_name)
        else:
            exc = UnknownFormatError(label_format=label_format)
            logging.exception(exc.message)
            raise exc

    # write Pascal VOC xml annotation for image
    xml_writer.save(os.path.join(annotations_output_dir, '{}.xml'.format(label_id)))


def _add_pascal_object_from_wkt(xml_writer, img_height, wkt_data, label):
    polygons = []
    if isinstance(wkt_data, list):  # V3+
        polygons = map(lambda x: wkt.loads(x['geometry']), wkt_data)
    else:  # V2
        polygons = wkt.loads(wkt_data)

    for point in polygons:
        xy_coords = []
        for x_val, y_val in point.exterior.coords:
            xy_coords.extend([x_val, img_height - y_val])
        # remove last polygon if it is identical to first point
        if xy_coords[-2:] == xy_coords[:2]:
            xy_coords = xy_coords[:-2]
        xml_writer.add_object(name=label, xy_coords=xy_coords)
    return xml_writer


def _add_pascal_object_from_xy(xml_writer, img_height, polygons, label):
    if not isinstance(polygons, list):
        # polygons is not [{'geometry': [xy]}] nor [[xy]]
        return xml_writer
    for polygon in polygons:
        if 'geometry' in polygon:  # V3
            polygon = polygon['geometry']
        if not isinstance(polygon, list) \
                or not all(map(lambda p: 'x' in p and 'y' in p, polygon)):
            # couldn't make a list of points, give up
            return xml_writer

        xy_coords = []
        for point in polygon:
            xy_coords.extend([point['x'], img_height - point['y']])
        xml_writer.add_object(name=label, xy_coords=xy_coords)
    return xml_writer
