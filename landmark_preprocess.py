import multiprocessing
import face_alignment
import cv2
import os
import time
import numpy as np
import argparse
import concurrent.futures
from Utils.misc import create_directory

parser = argparse.ArgumentParser(
    description='Landmark Processing Transcription')
parser.add_argument(
    'input', help="Folder containing all of the video id folders that contain face images")
parser.add_argument('output', help="Saves landmark images to this file path, in which folders 'real' and  \
                                    'fake' will be created. Inside of the real and fake folders, the labels \
                                    for the video will be created, in which folders for landmarks will be saved.")
parser.add_argument('--maxproc', default=20,
                    help="Maximum number of parallel processes.")

args = parser.parse_args()
total_videos = 0
count_processed = 0
MAX_PARALLEL_PROCS = int(args.maxproc)

'''
Landmarks from face_alignment are located as follows:
    Landmark | indices
    face:      0,  16
    eyebrow1:  17, 21
    eyebrow2:  22, 26 
    nose:      27, 30
    nostril:   31, 35
    eye1:      36, 41
    eye2:      42, 47
    lips:      48, 59
    teeth:     60, 67
'''


def transformation_from_points(points1, points2):
    points1 = points1.astype(np.float64)
    points2 = points2.astype(np.float64)

    c1 = np.mean(points1, axis=0)
    c2 = np.mean(points2, axis=0)
    points1 -= c1
    points2 -= c2
    s1 = np.std(points1)
    s2 = np.std(points2)
    points1 /= s1
    points2 /= s2

    U, S, Vt = np.linalg.svd(points1.T * points2)
    R = (U * Vt).T
    return np.vstack([np.hstack(((s2 / s1) * R,
                                 c2.T - (s2 / s1) * R * c1.T)),
                      np.matrix([0., 0., 1.])])


def get_position(size, padding=0.25):

    x = [0.000213256, 0.0752622, 0.18113, 0.29077, 0.393397, 0.586856, 0.689483, 0.799124,
         0.904991, 0.98004, 0.490127, 0.490127, 0.490127, 0.490127, 0.36688, 0.426036,
         0.490127, 0.554217, 0.613373, 0.121737, 0.187122, 0.265825, 0.334606, 0.260918,
         0.182743, 0.645647, 0.714428, 0.793132, 0.858516, 0.79751, 0.719335, 0.254149,
         0.340985, 0.428858, 0.490127, 0.551395, 0.639268, 0.726104, 0.642159, 0.556721,
         0.490127, 0.423532, 0.338094, 0.290379, 0.428096, 0.490127, 0.552157, 0.689874,
         0.553364, 0.490127, 0.42689]

    y = [0.106454, 0.038915, 0.0187482, 0.0344891, 0.0773906, 0.0773906, 0.0344891,
         0.0187482, 0.038915, 0.106454, 0.203352, 0.307009, 0.409805, 0.515625, 0.587326,
         0.609345, 0.628106, 0.609345, 0.587326, 0.216423, 0.178758, 0.179852, 0.231733,
         0.245099, 0.244077, 0.231733, 0.179852, 0.178758, 0.216423, 0.244077, 0.245099,
         0.780233, 0.745405, 0.727388, 0.742578, 0.727388, 0.745405, 0.780233, 0.864805,
         0.902192, 0.909281, 0.902192, 0.864805, 0.784792, 0.778746, 0.785343, 0.778746,
         0.784792, 0.824182, 0.831803, 0.824182]

    x, y = np.array(x), np.array(y)

    x = (x + padding) / (2 * padding + 1)
    y = (y + padding) / (2 * padding + 1)
    x = x * size
    y = y * size
    return np.array(list(zip(x, y)))


def get_frame(img_id):
    return img_id[-8:-4]


def get_landmarks_from_directory(path, fa):
    '''
    process the faces in a directory and find their landmark points
    '''
    faces = os.listdir(path)
    labels = []
    read_images = []
    for face in faces:
        read_images.append(cv2.imread(os.path.join(path, face)))
        labels.append(get_frame(face))
    read_images = list(filter(lambda im: not im is None, read_images))
    list_landmark_points = [fa.get_landmarks(
        img, detected_faces=[np.array([0, 0, 298, 298, 1])]) for img in read_images]

    return list_landmark_points, read_images, labels


def landmark_boundaries(front256, img):
    '''
    get the center of our landmarks and return the bounding box of the landmark
    *** function assumes the img is affine transformed ***
    '''
    x, y = front256[31:].mean(0).astype(np.int32)  # mouth
    mouth = get_landmark_box(img, x, y, 80, square=False)
    x, y = front256[10:19].mean(0).astype(np.int32)  # nose?
    nose = get_landmark_box(img, x, y, 40)
    # x, y =  np.concatenate([front256[0:5], front256[19: 25]]).mean(0).astype(np.int32) # eye1?
    # eye1 = get_landmark_box(img, x, y, 40)
    # x, y = np.concatenate([front256[5:10], front256[25: 31]]).mean(0).astype(np.int32) # eye2?
    # eye2 = get_landmark_box(img, x, y, 40)
    x, y = np.concatenate([front256[0:10], front256[19: 31]]).mean(
        0).astype(np.int32)  # both eyes
    eyes = get_landmark_box(img, x, y, 100, square=False)
    return mouth, nose, eyes


def get_landmark_box(img, x, y, w, square=True):
    if square:
        img = img[y - w: y + w, x - w: x + w, ...]
    else:
        img = img[y - w // 2: y + w // 2, x - w: x + w, ...]
    return img


def process_faces(fa, input_path, video_id, save_path):
    '''
    top level method that takes all of the faces in a directory, performs an affine\
        transformation, and extracts the mouth, nose, and eyes
    '''
    list_dir_landmarks, faces_array, labels = get_landmarks_from_directory(
        os.path.join(input_path, video_id), fa)
    front256 = get_position(256)
    count = 0

    create_directory(os.path.join(save_path, 'mouth'))
    create_directory(os.path.join(save_path, 'both-eyes'))
    create_directory(os.path.join(save_path, 'nose'))
    # create_directory(os.path.join(save_path, 'left-eye'))
    # create_directory(os.path.join(save_path, 'right-eye'))

    for frame, preds, face in zip(labels, list_dir_landmarks, faces_array):
        if preds is not None:
            # get the list of landmarks
            # shape = preds[0] # this command works on my computer, but not lewis
            # shape = preds[0][0] # this command works on Lewis, but not my computer
            # print(preds)

            shape = np.array(preds[0])
            shape = shape[17:]  # diregard the face endpoints

            M = transformation_from_points(
                np.matrix(shape), np.matrix(front256))  # transform the face

            img = cv2.warpAffine(face, M[:2], (256, 256))
            mouth, nose, eyes = landmark_boundaries(front256, img)

            # mouth = cv2.resize(mouth, (256, 128))
            # nose = cv2.resize(nose, (128, 128))
            # eye1 = cv2.resize(eye1, (128, 128))
            # eye2 = cv2.resize(eye2, (128, 128))
            # eyes = cv2.resize(eyes, (256, 128))

            cv2.imwrite(f'{save_path}/mouth/{frame}.jpg', mouth)
            cv2.imwrite(f'{save_path}/nose/{frame}.jpg', nose)
            # cv2.imwrite(f'{save_path}/left-eye/{frame}.jpg', eye1)
            # cv2.imwrite(f'{save_path}/right-eye/{frame}.jpg', eye2)
            cv2.imwrite(f'{save_path}/both-eyes/{frame}.jpg', eyes)

        else:
            count += 1
            print('No Preds:', count)


def process_video(fa, subfolder, vid):
    global count_processed
    output_path = create_directory(
        os.path.join(args.output, subfolder, vid))
    process_faces(fa, os.path.join(
        args.input, subfolder), vid, output_path)
    count_processed += 1
    return f'Finished processing video {vid}'


def split_list(l, n):
    return [l[i*n:i*n+n] for i in range(len(l) // n + 1)]


def main():
    global count_processed
    global total_videos
    file_type = '.mp4'
    # will fail if there is no folder labeled 'fake' and 'real'

    fa = face_alignment.FaceAlignment(
        face_alignment.LandmarksType._2D, device='cuda')

    start = time.time()
    subfolders = ['real', 'fake']
    landmark_args = []
    for subfolder in subfolders:
        file_list = [video_label for video_label in os.listdir(
            os.path.join(args.input, subfolder))]

        for vid in file_list:
            if os.path.exists(os.path.join(args.output, subfolder, vid)):
                continue
            landmark_args.append((subfolder, vid))

        total_videos += len(file_list)

    landmark_args_list = split_list(landmark_args, MAX_PARALLEL_PROCS)
    for landmark_args in landmark_args_list:
        with concurrent.futures.ProcessPoolExecutor(mp_context=multiprocessing.get_context('spawn')) as executor:
            results = [executor.submit(process_video, fa, subfolder, vid)
                       for subfolder, vid in landmark_args]

            for f in concurrent.futures.as_completed(results):
                print(f.result())

    process_time = time.time() - start
    print('PROCESS TIME: {:.3f} h for {} videos (out of {})'.format(
        process_time / 3600, count_processed, total_videos))


if __name__ == '__main__':
    main()
