import tensorflow as tf
import numpy as np
from scipy.misc import imresize
import glob
import json
import tqdm
import os

def write_dict(d, e, i, n):
    for k, v in e.items():
        try:
            d[k][i] = v
        except KeyError:
            shape = [n] + list(v.shape)
            dtype = 'int32' if k in ('caption', 'image_id') else 'uint8'
            d[k] = np.zeros(shape, dtype=dtype)
            d[k][i] = v

def crop_to_aspect_ratio(img):
    ''''''
    shape = tf.shape(img)
    print(shape)
    X = tf.minimum(shape[0], shape[1])
    return tf.image.resize_image_with_crop_or_pad(img, X, X)

def resize_lanczos(img, target_shape):
    def rs(x):
        if x.shape[-1] == 1:
            x = np.tile(x, [1, 1, 3])
        return imresize(x, target_shape, interp='lanczos')
    return tf.py_func(rs, [img], tf.uint8)

def _int64_feature(value):
    return tf.train.Feature(
        int64_list=tf.train.Int64List(value=[value]))

def _bytes_feature(value):
    return tf.train.Feature(
        bytes_list=tf.train.BytesList(value=[value]))

def read_imgs(img_files, size=224, center_crop=True, encode=True):
    t_files = tf.train.string_input_producer(img_files, shuffle=False)
    reader = tf.WholeFileReader()
    _, img_bytes = reader.read(t_files)
    img = tf.image.decode_jpeg(img_bytes)
    if center_crop:
        img = crop_to_aspect_ratio(img)
    rs = resize_lanczos(img, (size, size))
    if encode:
        return tf.image.encode_jpeg(rs)
    return rs
    
def read_captions(captions_json):
    j = json.loads(open(captions_json).read())
    ids, captions = zip(*sorted(j.items(), key=lambda x: int(x[0])))
    captions = np.array(captions)
    return tf.train.input_producer(captions, shuffle=False).dequeue(), captions.max()+1, captions.shape[-1], tf.train.input_producer([int(i) for i in ids], shuffle=False).dequeue()

def read_classes(classes_json):
    j = json.loads(open(classes_json).read())
    d = {int(d['image_id']): int(d['category_id']) for d in j['annotations']}
    _, classes = zip(*sorted(d.items(), key=lambda x: int(x[0])))
    return tf.train.input_producer(classes, shuffle=False).dequeue()

def main(imgs_path, captions_json, classes_json, output_file, numpy=False,
         lowshot_value=np.inf, img_size=299, center_crop=True, truncate=False):
    ''''''
    with tf.device('/CPU:0'):
        img_files = sorted(glob.glob(os.path.join(imgs_path, '*.jpg')))
        if truncate:
            img_files = img_files[:truncate]
        n = len(img_files)
        print('found {} images'.format(n))
        coord = tf.train.Coordinator()
        print('made coord')
        img_op = read_imgs(img_files, size=img_size, center_crop=center_crop, encode=not numpy)
        print('got img tensor')
        caption_op, vocab_size, length, id_op = read_captions(captions_json)
        print('got captions')
        class_op = read_classes(classes_json)
        print('got classes')
        if not numpy:
            record = tf.python_io.TFRecordWriter(output_file)
        else:
            record = {}
        if lowshot_value:
            fname = output_file.split('.')
            base_fname = '.'.join([fname[0]+'_BASE', fname[1]])
            novel_fname = '.'.join([fname[0]+'_NOVEL', fname[1]])
            if not numpy:
                base_record = tf.python_io.TFRecordWriter(base_fname)
                novel_record = tf.python_io.TFRecordWriter(novel_fname)
            else:
                base_record = {}
                novel_record = {}
            '''
            novel classes:
            --------------
            dining table, parking meter, remote, apple, potted plant, vase, broccoli, 
            spoon, keyboard, sports ball, hair drier, carrot, toilet, skateboard, 
            suitcase, tennis racket, baseball glove, donut, teddy bear, tv, motorcycle, 
            sandwich, bicycle, oven, toaster, car, cup, sheep, bed, refrigerator, tie, 
            horse, cat, wine glass, backpack, dog, boat, mouse, knife, baseball bat
            '''
            novel_classes = set([67, 14, 75, 53, 64, 86, 56, 50, 76, 37, 
                                 89, 57, 70, 41, 33, 43, 40, 60, 88, 72,  
                                  4, 54,  2, 79, 80,  3, 47, 20, 65, 82, 
                                 32, 19, 17, 46, 27, 18,  9, 74, 49, 39])
            novel_counts = dict(zip(list(novel_classes), [0]*40))
        print('starting')
        with tf.Session() as sess:
            tf.train.start_queue_runners(sess=sess, coord=coord)
            try:
                for i in tqdm.trange(n):
                    img, caption, class_, id_ = sess.run([img_op, caption_op, class_op, id_op])
                    if not numpy:
                        example = tf.train.Example(features=tf.train.Features(feature={
                            'image_size': _int64_feature(img_size),
                            'vocab_size': _int64_feature(vocab_size),
                            'length': _int64_feature(length),
                            'image': _bytes_feature(img),
                            'caption': _bytes_feature(caption.tostring()),
                            'class': _int64_feature(class_), 
                            'image_id': _int64_feature(id_),
                        }))
                        serial = example.SerializeToString()
                    else:
                        example = {'image': img, 
                                   'caption': caption, 
                                   'class': class_, 
                                   'image_id': id_}
                    if lowshot_value: 
                        if class_ in novel_classes:
                            if novel_counts[class_] < lowshot_value:
                                novel_counts[class_] += 1
                                if not numpy:
                                    record.write(serial)
                                else:
                                    write_dict(record, example, i, n)
                            if not numpy:
                                novel_record.write(serial)
                            else:
                                write_dict(novel_record, example, i, n)
                        else:
                            if not numpy:
                                record.write(serial)
                                base_record.write(serial)
                            else:
                                write_dict(record, example, i, n)
                                write_dict(base_record, example, i, n)
                    else:
                        if not numpy:
                            record.write(serial)
                        else:
                            write_dict(record, example, i, n)
                
                coord.request_stop()
                if not numpy:
                    record.close()
                    if lowshot_value:
                        novel_record.close()
                        base_record.close()
                else:
                    np.savez(output_file, **record)
                    if lowshot_value:
                        np.savez(base_fname, **base_record)
                        np.savez(novel_fname, **novel_record)
                        
                
            except:
                coord.request_stop()
                raise
    
if __name__== '__main__':
    import argparse
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('imgs_path', type=str)
    p.add_argument('captions_json', type=str)
    p.add_argument('classes_json', type=str)
    p.add_argument('output_file', type=str)
    p.add_argument('-lv', '--lowshot_value', type=int, default=np.inf,
        help='number of images per novel class')
    p.add_argument('-s', '--img_size', type=int, default=299,
        help='sidelength of images')
    p.add_argument('-c', '--center_crop', type=bool, default=True,
        help='whether or not to perform a center crop before resize')
    p.add_argument('-t', '--truncate', type=int, default=False,
        help='whether to truncate the total number of examples')
    d = p.parse_args().__dict__
    main(**d)
